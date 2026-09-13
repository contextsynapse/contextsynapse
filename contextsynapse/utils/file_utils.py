"""
AIContextDB File Utilities
Self-contained file utilities for data processing and management.
Extracted and adapted from QGraph File Utilities.
"""

import os
import json
import csv
import pandas as pd
from pathlib import Path
from typing import Dict, List, Any, Optional, Union, Iterator
import gzip
import pickle
from datetime import datetime
import hashlib
import logging

logger = logging.getLogger(__name__)

class AIContextDBFileUtils:
    """AIContextDB utility functions for file operations."""
    
    @staticmethod
    def ensure_dir(path: str) -> Path:
        """Ensure directory exists, create if not."""
        try:
            path = Path(path)
            path.mkdir(parents=True, exist_ok=True)
            return path
        except Exception as e:
            logger.error(f"[EMOJI] Failed to create directory {path}: {e}")
            raise
    
    @staticmethod
    def get_file_hash(filepath: str) -> str:
        """Calculate MD5 hash of a file."""
        try:
            hash_md5 = hashlib.md5()
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            logger.error(f"[EMOJI] Failed to calculate file hash: {e}")
            return ""
    
    @staticmethod
    def save_json(data: Any, filepath: str, indent: int = 2) -> bool:
        """Save data to JSON file."""
        try:
            AIContextDBFileUtils.ensure_dir(os.path.dirname(filepath))
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False, default=str)
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to save JSON: {e}")
            return False
    
    @staticmethod
    def load_json(filepath: str) -> Optional[Any]:
        """Load data from JSON file."""
        try:
            if not os.path.exists(filepath):
                return None
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to load JSON: {e}")
            return None
    
    @staticmethod
    def save_pickle(data: Any, filepath: str) -> bool:
        """Save data to pickle file."""
        try:
            AIContextDBFileUtils.ensure_dir(os.path.dirname(filepath))
            with open(filepath, 'wb') as f:
                pickle.dump(data, f)
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to save pickle: {e}")
            return False
    
    @staticmethod
    def load_pickle(filepath: str) -> Optional[Any]:
        """Load data from pickle file."""
        try:
            if not os.path.exists(filepath):
                return None
            with open(filepath, 'rb') as f:
                return pickle.load(f)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to load pickle: {e}")
            return None
    
    @staticmethod
    def save_csv(data: List[Dict[str, Any]], filepath: str) -> bool:
        """Save data to CSV file."""
        try:
            if not data:
                return False
            
            AIContextDBFileUtils.ensure_dir(os.path.dirname(filepath))
            
            # Get fieldnames from first row
            fieldnames = list(data[0].keys())
            
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(data)
            
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to save CSV: {e}")
            return False
    
    @staticmethod
    def load_csv(filepath: str) -> List[Dict[str, Any]]:
        """Load data from CSV file."""
        try:
            if not os.path.exists(filepath):
                return []
            
            data = []
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    data.append(dict(row))
            
            return data
        except Exception as e:
            logger.error(f"[EMOJI] Failed to load CSV: {e}")
            return []
    
    @staticmethod
    def get_file_size(filepath: str) -> int:
        """Get file size in bytes."""
        try:
            return os.path.getsize(filepath)
        except Exception as e:
            logger.error(f"[EMOJI] Failed to get file size: {e}")
            return 0
    
    @staticmethod
    def list_files(directory: str, pattern: str = "*", recursive: bool = False) -> List[str]:
        """List files in directory."""
        try:
            path = Path(directory)
            if not path.exists():
                return []
            
            if recursive:
                files = list(path.rglob(pattern))
            else:
                files = list(path.glob(pattern))
            
            return [str(f) for f in files if f.is_file()]
        except Exception as e:
            logger.error(f"[EMOJI] Failed to list files: {e}")
            return []
    
    @staticmethod
    def delete_file(filepath: str) -> bool:
        """Delete a file."""
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                return True
            return False
        except Exception as e:
            logger.error(f"[EMOJI] Failed to delete file: {e}")
            return False
    
    @staticmethod
    def copy_file(src: str, dst: str) -> bool:
        """Copy a file."""
        try:
            import shutil
            AIContextDBFileUtils.ensure_dir(os.path.dirname(dst))
            shutil.copy2(src, dst)
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to copy file: {e}")
            return False
    
    @staticmethod
    def compress_file(filepath: str, compressed_path: str = None) -> bool:
        """Compress a file using gzip."""
        try:
            if compressed_path is None:
                compressed_path = filepath + '.gz'
            
            AIContextDBFileUtils.ensure_dir(os.path.dirname(compressed_path))
            
            with open(filepath, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    f_out.writelines(f_in)
            
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to compress file: {e}")
            return False
    
    @staticmethod
    def decompress_file(compressed_path: str, output_path: str = None) -> bool:
        """Decompress a gzip file."""
        try:
            if output_path is None:
                output_path = compressed_path[:-3] if compressed_path.endswith('.gz') else compressed_path + '_decompressed'
            
            AIContextDBFileUtils.ensure_dir(os.path.dirname(output_path))
            
            with gzip.open(compressed_path, 'rb') as f_in:
                with open(output_path, 'wb') as f_out:
                    f_out.writelines(f_in)
            
            return True
        except Exception as e:
            logger.error(f"[EMOJI] Failed to decompress file: {e}")
            return False

class AIContextDBLogUtils:
    """AIContextDB logging utilities."""
    
    @staticmethod
    def setup_logging(log_level: str = "INFO", log_file: str = None) -> logging.Logger:
        """Setup logging configuration."""
        try:
            # Create logger
            logger = logging.getLogger("contextsynapse")
            logger.setLevel(getattr(logging, log_level.upper()))
            
            # Clear existing handlers
            logger.handlers.clear()
            
            # Create formatter
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            
            # Console handler
            console_handler = logging.StreamHandler()
            console_handler.setLevel(getattr(logging, log_level.upper()))
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            
            # File handler (if specified)
            if log_file:
                AIContextDBFileUtils.ensure_dir(os.path.dirname(log_file))
                file_handler = logging.FileHandler(log_file)
                file_handler.setLevel(getattr(logging, log_level.upper()))
                file_handler.setFormatter(formatter)
                logger.addHandler(file_handler)
            
            return logger
            
        except Exception as e:
            print(f"[EMOJI] Failed to setup logging: {e}")
            return logging.getLogger("contextsynapse")
    
    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """Get a logger instance."""
        return logging.getLogger(f"contextsynapse.{name}")

# Global utility instances
file_utils = AIContextDBFileUtils()
log_utils = AIContextDBLogUtils()

def get_file_utils() -> AIContextDBFileUtils:
    """Get the global file utils instance."""
    return file_utils

def get_log_utils() -> AIContextDBLogUtils:
    """Get the global log utils instance."""
    return log_utils











