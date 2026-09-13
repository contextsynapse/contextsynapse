"""
Storage Strategy Configuration

Defines different storage strategies:
1. Single-file HDF5 (Oracle-type): All data in one .h5 file
2. Multi-file optimized: Different file types for different data (JSON, Parquet, NPZ)
"""

from enum import Enum
from dataclasses import dataclass
from typing import Dict, Any, Optional


class StorageStrategy(Enum):
    """Storage strategy options."""
    SINGLE_FILE = "single_file"  # All data in one HDF5 file (Oracle-type .dbf)
    MULTI_FILE_OPTIMIZED = "multi_file_optimized"  # Different file types for different data
    MULTI_FILE_LEGACY = "multi_file_legacy"  # Legacy multi-file format (backward compatibility)


@dataclass
class SingleFileConfig:
    """Configuration for single-file HDF5 storage."""
    enabled: bool = True
    file_extension: str = ".h5"  # HDF5 file extension
    compression: str = "gzip"  # Compression: gzip, lz4, szip, none
    compression_level: int = 6  # Compression level (0-9)
    chunk_size: Optional[int] = None  # Chunk size for HDF5 datasets (None = auto)
    enable_metadata: bool = True  # Store metadata in HDF5
    enable_csr: bool = True  # Store CSR matrices in HDF5


@dataclass
class MultiFileOptimizedConfig:
    """Configuration for multi-file optimized storage (different formats for different data types)."""
    enabled: bool = True
    
    # Graph structure (nodes, edges, relationships)
    graph_format: str = "csr"  # Options: csr (NPZ - default, optimized), json (human-readable fallback)
    graph_compression: bool = True  # Compression for CSR graph
    
    # Node/Edge properties (tabular data)
    properties_format: str = "parquet"  # Options: parquet, arrow, csv
    properties_compression: str = "snappy"  # Compression: snappy, gzip, lz4, zstd
    
    # CSR matrices (sparse graph data)
    csr_format: str = "npz"  # Options: npz, hdf5, pickle
    csr_compression: bool = True  # Enable compression for CSR
    
    # Vector embeddings
    vectors_format: str = "npy"  # Options: npy, hdf5, parquet
    vectors_compression: bool = True
    
    # Images/Multimodal
    images_format: str = "hdf5"  # Options: hdf5, png, jpeg (for thumbnails)
    images_compression: str = "gzip"
    
    # Tables/Tabular data
    tables_format: str = "parquet"  # Options: parquet, arrow, csv
    tables_compression: str = "snappy"
    
    # Metadata
    metadata_format: str = "json"  # Options: json, yaml, toml
    metadata_compression: bool = False


@dataclass
class StorageStrategyConfig:
    """
    Complete storage strategy configuration.
    
    Allows choosing between:
    - Single-file HDF5: All data in one file (portable, atomic)
    - Multi-file optimized: Different formats for different data types (performance-optimized)
    """
    strategy: StorageStrategy = StorageStrategy.SINGLE_FILE
    
    # Single-file configuration
    single_file: SingleFileConfig = None
    
    # Multi-file optimized configuration
    multi_file_optimized: MultiFileOptimizedConfig = None
    
    # Base directory for storage
    base_path: str = "contextcore_data"
    
    # Namespace-specific path
    namespace_path: Optional[str] = None
    
    def __post_init__(self):
        """Initialize default configs if not provided."""
        if self.single_file is None:
            self.single_file = SingleFileConfig()
        if self.multi_file_optimized is None:
            self.multi_file_optimized = MultiFileOptimizedConfig()
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> 'StorageStrategyConfig':
        """Create StorageStrategyConfig from dictionary."""
        strategy_str = config_dict.get('strategy', 'single_file')
        try:
            strategy = StorageStrategy(strategy_str)
        except ValueError:
            strategy = StorageStrategy.SINGLE_FILE
        
        single_file_config = None
        if 'single_file' in config_dict:
            single_file_config = SingleFileConfig(**config_dict['single_file'])
        
        multi_file_config = None
        if 'multi_file_optimized' in config_dict:
            multi_file_config = MultiFileOptimizedConfig(**config_dict['multi_file_optimized'])
        
        return cls(
            strategy=strategy,
            single_file=single_file_config,
            multi_file_optimized=multi_file_config,
            base_path=config_dict.get('base_path', 'contextcore_data'),
            namespace_path=config_dict.get('namespace_path')
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            'strategy': self.strategy.value,
            'base_path': self.base_path,
        }
        
        if self.namespace_path:
            result['namespace_path'] = self.namespace_path
        
        if self.single_file:
            result['single_file'] = {
                'enabled': self.single_file.enabled,
                'file_extension': self.single_file.file_extension,
                'compression': self.single_file.compression,
                'compression_level': self.single_file.compression_level,
                'chunk_size': self.single_file.chunk_size,
                'enable_metadata': self.single_file.enable_metadata,
                'enable_csr': self.single_file.enable_csr,
            }
        
        if self.multi_file_optimized:
            result['multi_file_optimized'] = {
                'enabled': self.multi_file_optimized.enabled,
                'graph_format': self.multi_file_optimized.graph_format,
                'graph_compression': self.multi_file_optimized.graph_compression,
                'properties_format': self.multi_file_optimized.properties_format,
                'properties_compression': self.multi_file_optimized.properties_compression,
                'csr_format': self.multi_file_optimized.csr_format,
                'csr_compression': self.multi_file_optimized.csr_compression,
                'vectors_format': self.multi_file_optimized.vectors_format,
                'vectors_compression': self.multi_file_optimized.vectors_compression,
                'images_format': self.multi_file_optimized.images_format,
                'images_compression': self.multi_file_optimized.images_compression,
                'tables_format': self.multi_file_optimized.tables_format,
                'tables_compression': self.multi_file_optimized.tables_compression,
                'metadata_format': self.multi_file_optimized.metadata_format,
                'metadata_compression': self.multi_file_optimized.metadata_compression,
            }
        
        return result


# Storage strategy recommendations
STORAGE_STRATEGY_GUIDE = {
    "single_file": {
        "description": "All data in one HDF5 file (Oracle-type .dbf)",
        "best_for": [
            "Portability (easy backup/restore)",
            "Atomic operations",
            "Simplified deployment",
            "Cross-platform compatibility"
        ],
        "tradeoffs": {
            "pros": ["Single file", "Atomic writes", "Portable", "Easy backup"],
            "cons": ["Slightly slower for large datasets", "Less granular access"]
        }
    },
    "multi_file_optimized": {
        "description": "Different file formats optimized for each data type",
        "best_for": [
            "Maximum performance",
            "Large-scale datasets",
            "Selective loading",
            "Format-specific optimizations"
        ],
        "file_types": {
            "graph": "JSON - Human-readable, good for structure",
            "properties": "Parquet - Columnar, compressed, fast queries",
            "csr": "NPZ - Optimized for sparse matrices",
            "vectors": "NPY - Fast NumPy arrays",
            "images": "HDF5 - Efficient for large binary data",
            "tables": "Parquet - Columnar analytics"
        },
        "tradeoffs": {
            "pros": ["Optimized per data type", "Selective loading", "Better performance"],
            "cons": ["Multiple files", "More complex backup"]
        }
    }
}

