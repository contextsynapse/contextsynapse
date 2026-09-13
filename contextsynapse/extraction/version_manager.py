"""
Version Manager

Handles file checksums, versioning, and rerun strategies.
"""

from pathlib import Path
from typing import Optional, Dict, Any, List
import hashlib
import json
import logging
from datetime import datetime

from .file_manager import FileManager

logger = logging.getLogger(__name__)


def compute_file_hash(file_path: str, algorithm: str = "sha256") -> str:
    """
    Compute hash of a file or URL.
    
    Args:
        file_path: Path to file or URL
        algorithm: Hash algorithm (sha256, md5, etc.)
        
    Returns:
        Hash string in format: "{algorithm}-{hex_digest}"
    """
    # Check if it's a URL
    file_path_str = str(file_path).lower().strip()
    is_url = (
        file_path_str.startswith("http://") or
        file_path_str.startswith("https://") or
        file_path_str.startswith("www.")
    )
    
    hash_obj = hashlib.new(algorithm)
    
    if is_url:
        # For URLs, hash the URL string itself
        hash_obj.update(file_path.encode('utf-8'))
    else:
        # For files, hash the file content
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_obj.update(chunk)
    
    hex_digest = hash_obj.hexdigest()
    return f"{algorithm}-{hex_digest}"


class VersionManager:
    """
    Manages versioning and rerun strategies for extracted documents.
    
    Tracks:
    - File hashes
    - Extractor versions
    - Normalized JSON versions
    - Page extraction history
    """
    
    def __init__(self, file_manager: FileManager):
        """
        Initialize version manager.
        
        Args:
            file_manager: File manager instance
        """
        self.file_manager = file_manager
        self.version_file = file_manager.workspace / "normalized" / ".versions.json"
        self._versions: Dict[str, Any] = self._load_versions()
    
    def _load_versions(self) -> Dict[str, Any]:
        """Load version history from disk."""
        if self.version_file.exists():
            try:
                with open(self.version_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load versions: {e}")
                return {}
        return {}
    
    def _save_versions(self) -> None:
        """Save version history to disk."""
        self.version_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.version_file, 'w', encoding='utf-8') as f:
            json.dump(self._versions, f, indent=2, ensure_ascii=False)
    
    def get_file_hash(self, file_path: str) -> str:
        """
        Get or compute file hash.
        
        Args:
            file_path: Path to file
            
        Returns:
            Hash string
        """
        file_key = str(Path(file_path).absolute())
        
        # Check cache
        if "file_hashes" in self._versions:
            if file_key in self._versions["file_hashes"]:
                return self._versions["file_hashes"][file_key]
        
        # Compute hash
        hash_value = compute_file_hash(file_path)
        
        # Cache it
        if "file_hashes" not in self._versions:
            self._versions["file_hashes"] = {}
        self._versions["file_hashes"][file_key] = hash_value
        self._save_versions()
        
        return hash_value
    
    def check_existing_version(
        self,
        file_hash: str,
        extractor_version: str,
        pages_mode: str,
        requested_pages: Optional[List[int]] = None
    ) -> Optional[str]:
        """
        Check if extraction already exists for this configuration.
        
        Args:
            file_hash: File hash
            extractor_version: Extractor version
            pages_mode: Extraction mode (full, range, list)
            requested_pages: List of requested pages (if applicable)
            
        Returns:
            Existing version string (e.g., "v1") or None if not found
        """
        if "extractions" not in self._versions:
            return None
        
        for version, extraction_info in self._versions["extractions"].items():
            if (extraction_info.get("file_hash") == file_hash and
                extraction_info.get("extractor_version") == extractor_version):
                
                # Check pages match
                if pages_mode == "full":
                    # Full extraction - check if it exists
                    if extraction_info.get("pages_mode") == "full":
                        return version
                elif pages_mode == "range" or pages_mode == "list":
                    # Check if requested pages are subset of existing
                    existing_pages = extraction_info.get("extracted_pages", [])
                    if requested_pages:
                        if all(p in existing_pages for p in requested_pages):
                            return version
        
        return None
    
    def get_next_version(self) -> str:
        """
        Get next version number.
        
        Returns:
            Version string (e.g., "v1", "v2")
        """
        if "extractions" not in self._versions:
            return "v1"
        
        # Find highest version number
        max_version = 0
        for version in self._versions["extractions"].keys():
            if version.startswith("v") and version[1:].isdigit():
                version_num = int(version[1:])
                max_version = max(max_version, version_num)
        
        return f"v{max_version + 1}"
    
    def register_extraction(
        self,
        version: str,
        file_hash: str,
        extractor_version: str,
        pages_mode: str,
        extracted_pages: List[int],
        normalized_file: str
    ) -> None:
        """
        Register a new extraction version.
        
        Args:
            version: Version string (e.g., "v1")
            file_hash: File hash
            extractor_version: Extractor version used
            pages_mode: Extraction mode
            extracted_pages: List of extracted page numbers
            normalized_file: Path to normalized JSON file
        """
        if "extractions" not in self._versions:
            self._versions["extractions"] = {}
        
        self._versions["extractions"][version] = {
            "file_hash": file_hash,
            "extractor_version": extractor_version,
            "pages_mode": pages_mode,
            "extracted_pages": extracted_pages,
            "normalized_file": normalized_file,
            "created_at": datetime.utcnow().isoformat() + "Z",
        }
        
        self._save_versions()
        logger.info(f"Registered extraction version: {version}")
    
    def archive_version(self, version: str) -> None:
        """
        Archive a version (move to history).
        
        Args:
            version: Version to archive
        """
        if "extractions" not in self._versions:
            return
        
        if version in self._versions["extractions"]:
            extraction_info = self._versions["extractions"][version]
            normalized_file = Path(extraction_info.get("normalized_file", ""))
            
            if normalized_file.exists():
                # Move to history
                self.file_manager.archive_normalized(version)
                logger.info(f"Archived version {version} to history")
    
    def merge_pages(
        self,
        existing_version: str,
        new_pages: List[int],
        new_extraction_result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Merge new pages into existing normalized document.
        
        Args:
            existing_version: Existing version to merge into
            new_pages: List of new page numbers
            new_extraction_result: New extraction result
            
        Returns:
            Merged normalized document dictionary
        """
        # Load existing normalized document
        if "extractions" not in self._versions:
            raise ValueError(f"Version {existing_version} not found")
        
        extraction_info = self._versions["extractions"].get(existing_version)
        if not extraction_info:
            raise ValueError(f"Version {existing_version} not found")
        
        normalized_file = Path(extraction_info["normalized_file"])
        if not normalized_file.exists():
            raise FileNotFoundError(f"Normalized file not found: {normalized_file}")
        
        with open(normalized_file, 'r', encoding='utf-8') as f:
            existing_doc = json.load(f)
        
        # Merge new pages
        existing_pages = {p.get("page_no"): p for p in existing_doc.get("content", {}).get("pages", [])}
        
        for new_page in new_extraction_result.get("pages", []):
            page_no = new_page.get("page_no")
            if page_no not in existing_pages:
                existing_doc["content"]["pages"].append(new_page)
                existing_pages[page_no] = new_page
        
        # Update extraction scope
        all_pages = sorted(set(
            existing_doc["extraction_scope"].get("extracted_pages", []) + new_pages
        ))
        existing_doc["extraction_scope"]["extracted_pages"] = all_pages
        existing_doc["extraction_scope"]["total_pages_extracted"] = len(all_pages)
        
        # Update metadata
        existing_doc["status"]["last_modified"] = datetime.utcnow().isoformat() + "Z"
        
        return existing_doc
    
    def list_versions(self) -> List[str]:
        """
        List all extraction versions.
        
        Returns:
            List of version strings
        """
        if "extractions" not in self._versions:
            return []
        
        versions = list(self._versions["extractions"].keys())
        # Sort by version number
        versions.sort(key=lambda v: int(v[1:]) if v.startswith("v") and v[1:].isdigit() else 0)
        return versions
    
    def get_version_info(self, version: str) -> Optional[Dict[str, Any]]:
        """
        Get information about a specific version.
        
        Args:
            version: Version string
            
        Returns:
            Version information dictionary or None
        """
        if "extractions" not in self._versions:
            return None
        
        return self._versions["extractions"].get(version)
