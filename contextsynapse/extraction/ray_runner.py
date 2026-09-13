"""
Ray Runner

Ray-based scaling utilities for parallel extraction.
"""

from typing import List, Optional, Dict, Any
import logging
import os

try:
    import ray
    RAY_AVAILABLE = True
except ImportError:
    RAY_AVAILABLE = False
    # Create a dummy ray object to avoid AttributeError
    class DummyRay:
        @staticmethod
        def remote(func):
            return func
        @staticmethod
        def is_initialized():
            return False
        @staticmethod
        def init(*args, **kwargs):
            pass
        @staticmethod
        def get(futures):
            return [f() if callable(f) else f for f in futures]
        @staticmethod
        def shutdown():
            pass
    ray = DummyRay()

from .config import ExtractionConfig
from .extract_engine import run_extraction

logger = logging.getLogger(__name__)


def _init_ray_if_needed() -> bool:
    """
    Initialize Ray if not already initialized.
    
    Returns:
        True if Ray is available and initialized, False otherwise
    """
    if not RAY_AVAILABLE:
        logger.warning("Ray is not available. Install with: pip install ray")
        return False
    
    if not ray.is_initialized():
        try:
            ray.init(ignore_reinit_error=True)
            logger.info("Ray initialized successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Ray: {e}")
            return False
    
    return True


@ray.remote
def remote_extract_document(config_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remote Ray task for document extraction.
    
    Args:
        config_dict: ExtractionConfig as dictionary
        
    Returns:
        Dictionary with document_id and status
    """
    try:
        config = ExtractionConfig.from_dict(config_dict)
        document_id = run_extraction(config)
        return {
            "success": True,
            "document_id": document_id,
            "error": None
        }
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        return {
            "success": False,
            "document_id": None,
            "error": str(e)
        }


def extract_many_documents_with_ray(
    configs: List[ExtractionConfig],
    num_cpus: Optional[int] = None,
    num_gpus: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Extract multiple documents in parallel using Ray.
    
    Args:
        configs: List of extraction configurations
        num_cpus: Number of CPUs to use (optional)
        num_gpus: Number of GPUs to use (optional)
        
    Returns:
        List of results, each containing document_id and status
    """
    if not _init_ray_if_needed():
        logger.warning("Ray not available, falling back to sequential extraction")
        results = []
        for config in configs:
            try:
                document_id = run_extraction(config)
                results.append({
                    "success": True,
                    "document_id": document_id,
                    "error": None
                })
            except Exception as e:
                results.append({
                    "success": False,
                    "document_id": None,
                    "error": str(e)
                })
        return results
    
    # Convert configs to dictionaries for Ray serialization
    config_dicts = [config.to_dict() for config in configs]
    
    # Submit tasks
    logger.info(f"Submitting {len(config_dicts)} extraction tasks to Ray")
    futures = [remote_extract_document.remote(cfg_dict) for cfg_dict in config_dicts]
    
    # Get results
    results = ray.get(futures)
    
    # Log summary
    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful
    logger.info(f"Extraction complete: {successful} successful, {failed} failed")
    
    return results


def extract_folder_with_ray(
    folder_path: str,
    reader: str = "AUTO",
    detect: Optional[List[str]] = None,
    output_dir: str = "contextcore_data",
    num_cpus: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Extract all supported files from a folder using Ray.
    
    Args:
        folder_path: Path to folder containing files
        reader: Extractor to use (default: "AUTO")
        detect: List of modalities to detect (default: ["TEXT"])
        output_dir: Output directory
        num_cpus: Number of CPUs to use
        
    Returns:
        List of extraction results
    """
    from pathlib import Path
    
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder_path}")
    
    # Find all supported files
    supported_extensions = {
        ".pdf", ".docx", ".pptx", ".csv", ".txt", ".html", ".htm",
        ".png", ".jpg", ".jpeg", ".mp3", ".wav", ".mp4", ".avi"
    }
    
    files = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in supported_extensions
    ]
    
    if not files:
        logger.warning(f"No supported files found in {folder_path}")
        return []
    
    logger.info(f"Found {len(files)} files to extract")
    
    # Create configs
    configs = [
        ExtractionConfig(
            file_path=str(f),
            reader=reader,
            detect=detect or ["TEXT"],
            output_dir=output_dir
        )
        for f in files
    ]
    
    # Run with Ray
    return extract_many_documents_with_ray(configs, num_cpus=num_cpus)


def shutdown_ray() -> None:
    """Shutdown Ray cluster."""
    if RAY_AVAILABLE and ray.is_initialized():
        ray.shutdown()
        logger.info("Ray shutdown complete")
