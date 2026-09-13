"""
File Manager

Handles storage layout, paths, and manifest generation.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import logging
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


def get_document_workspace(document_id: str, output_dir: str = "contextcore_data", namespace: Optional[str] = None) -> Path:
    """
    Get the workspace directory for a document.
    
    Args:
        document_id: Document identifier
        output_dir: Base output directory
        namespace: Optional namespace for namespace-centric storage
        
    Returns:
        Path to document workspace
    """
    base_path = Path(output_dir)
    if namespace:
        # Namespace-centric structure: base_dir/namespaces/{namespace}/documents/{document_id}
        return base_path / "namespaces" / namespace / "documents" / document_id
    else:
        # Legacy structure: base_dir/{document_id}
        return base_path / document_id


class FileManager:
    """
    Manages file storage layout and manifest for extracted documents.
    """
    
    def __init__(self, document_id: str, output_dir: str = "contextcore_data", namespace: Optional[str] = None):
        """
        Initialize file manager.
        
        Args:
            document_id: Document identifier
            output_dir: Base output directory
            namespace: Optional namespace for namespace-centric storage
        """
        self.document_id = document_id
        self.output_dir = Path(output_dir)
        self.namespace = namespace
        self.workspace = get_document_workspace(document_id, output_dir, namespace)
        self._ensure_structure()
    
    def _ensure_structure(self) -> None:
        """Create directory structure if it doesn't exist."""
        directories = [
            self.workspace / "raw",
            self.workspace / "extracted" / "pages",
            self.workspace / "extracted" / "tables",
            self.workspace / "extracted" / "images",
            self.workspace / "extracted" / "audio",
            self.workspace / "extracted" / "video_frames",
            self.workspace / "extracted" / "web",
            self.workspace / "normalized",
            self.workspace / "history",
            self.workspace / "logs",
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    def store_raw_file(self, source_path: str) -> Path:
        """
        Copy original file to raw/ directory.
        
        Args:
            source_path: Path to source file
            
        Returns:
            Path to stored file
        """
        source = Path(source_path)
        dest = self.workspace / "raw" / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(source, dest)
        logger.info(f"Stored raw file: {dest}")
        return dest
    
    def store_page(self, page_no: int, content: str) -> Path:
        """
        Store extracted page text.
        
        Args:
            page_no: Page number (1-indexed)
            content: Page text content
            
        Returns:
            Path to stored file
        """
        filename = f"page_{page_no:03d}.txt"
        file_path = self.workspace / "extracted" / "pages" / filename
        file_path.write_text(content, encoding="utf-8")
        return file_path.relative_to(self.workspace)
    
    def store_table(self, table_id: str, table_data: List[List[str]]) -> Path:
        """
        Store extracted table as CSV.
        
        Args:
            table_id: Table identifier
            table_data: Table data (list of rows)
            
        Returns:
            Path to stored file (relative to workspace)
        """
        import csv
        filename = f"tbl_{table_id}.csv"
        file_path = self.workspace / "extracted" / "tables" / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerows(table_data)
        
        return file_path.relative_to(self.workspace)
    
    def store_image(self, image_id: str, image_path: str) -> Path:
        """
        Store extracted image file.
        
        Args:
            image_id: Image identifier
            image_path: Path to source image file
            
        Returns:
            Path to stored file (relative to workspace)
        """
        source = Path(image_path)
        ext = source.suffix or ".png"
        filename = f"img_{image_id}{ext}"
        dest = self.workspace / "extracted" / "images" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(source, dest)
        return dest.relative_to(self.workspace)
    
    def store_audio(self, audio_id: str, audio_path: str) -> Path:
        """
        Store extracted audio file.
        
        Args:
            audio_id: Audio identifier
            audio_path: Path to source audio file
            
        Returns:
            Path to stored file (relative to workspace)
        """
        source = Path(audio_path)
        ext = source.suffix or ".wav"
        filename = f"audio_{audio_id}{ext}"
        dest = self.workspace / "extracted" / "audio" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(source, dest)
        return dest.relative_to(self.workspace)
    
    def store_video_frame(self, frame_no: int, frame_path: str) -> Path:
        """
        Store extracted video frame.
        
        Args:
            frame_no: Frame number
            frame_path: Path to source frame image
            
        Returns:
            Path to stored file (relative to workspace)
        """
        source = Path(frame_path)
        ext = source.suffix or ".png"
        filename = f"frame_{frame_no:04d}{ext}"
        dest = self.workspace / "extracted" / "video_frames" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        
        import shutil
        shutil.copy2(source, dest)
        return dest.relative_to(self.workspace)
    
    def store_web_content(self, web_id: str, html_content: str) -> Path:
        """
        Store extracted web content.
        
        Args:
            web_id: Web content identifier
            html_content: HTML content
            
        Returns:
            Path to stored file (relative to workspace)
        """
        filename = f"page_{web_id}.html"
        file_path = self.workspace / "extracted" / "web" / filename
        file_path.write_text(html_content, encoding="utf-8")
        return file_path.relative_to(self.workspace)
    
    def save_manifest(self, manifest: Dict[str, Any]) -> Path:
        """
        Save extraction manifest.
        
        Args:
            manifest: Manifest dictionary
            
        Returns:
            Path to manifest file
        """
        manifest_file = self.workspace / "extracted" / "manifest.json"
        with open(manifest_file, 'w', encoding='utf-8') as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        return manifest_file
    
    def load_manifest(self) -> Optional[Dict[str, Any]]:
        """
        Load extraction manifest.
        
        Returns:
            Manifest dictionary or None if not found
        """
        manifest_file = self.workspace / "extracted" / "manifest.json"
        if manifest_file.exists():
            with open(manifest_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return None
    
    def save_normalized(self, normalized_doc: Dict[str, Any], version: str = "v1", namespace: str = "default", use_parquet: bool = True) -> Path:
        """
        Save normalized document to both Parquet and JSON formats.
        
        Args:
            normalized_doc: Normalized document dictionary
            version: Version string (e.g., "v1", "v2")
            namespace: Namespace name
            use_parquet: Whether to use Parquet format (default: True)
            
        Returns:
            Path to normalized Parquet file
        """
        document_id = normalized_doc.get('document_id', self.document_id)
        normalized_dir = self.workspace / "normalized"
        normalized_dir.mkdir(parents=True, exist_ok=True)
        
        # Always save JSON file for human readability and debugging
        json_filename = f"normalized_{version}.json"
        json_path = normalized_dir / json_filename
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(normalized_doc, f, indent=2, ensure_ascii=False)
            logger.info(f"Stored normalized document to JSON: {document_id}/{version}")
        except Exception as e:
            logger.warning(f"JSON storage failed: {e}")
        
        # Save Parquet file if requested (for efficient processing)
        if use_parquet:
            try:
                from .parquet_writer import save_normalized_to_parquet
                
                parquet_filename = f"normalized_{version}.parquet"
                parquet_path = normalized_dir / parquet_filename
                
                # Save to Parquet incrementally
                parquet_path = save_normalized_to_parquet(
                    normalized_doc=normalized_doc,
                    output_path=parquet_path,
                    version=version,
                    compression="zstd",
                    batch_size=500
                )
                
                logger.info(f"Stored normalized document to Parquet: {document_id}/{version}")
                return parquet_path
            except Exception as e:
                logger.error(f"Parquet storage failed: {e}")
                # Return JSON path as fallback
                return json_path
        
        # If Parquet not requested, return JSON path
        return json_path
    
    def archive_normalized(self, version: str) -> None:
        """
        Move normalized Parquet file to history folder.
        
        Args:
            version: Version to archive
        """
        # Try Parquet first
        source = self.workspace / "normalized" / f"normalized_{version}.parquet"
        if not source.exists():
            # Fallback to JSON for backward compatibility
            source = self.workspace / "normalized" / f"normalized_{version}.json"
        
        if source.exists():
            dest = self.workspace / "history" / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.move(str(source), str(dest))
            logger.info(f"Archived {source.name} to history")
    
    def get_log_file(self, timestamp: Optional[str] = None) -> Path:
        """
        Get log file path.
        
        Args:
            timestamp: Optional timestamp string (auto-generated if None)
            
        Returns:
            Path to log file
        """
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"extract_{timestamp}.log"
        return self.workspace / "logs" / filename
    
    def list_normalized_versions(self) -> List[str]:
        """
        List all normalized versions (Parquet or JSON).
        
        Returns:
            List of version strings (e.g., ["v1", "v2"])
        """
        normalized_dir = self.workspace / "normalized"
        if not normalized_dir.exists():
            return []
        
        versions = []
        # Check for Parquet files first
        for file in normalized_dir.glob("normalized_*.parquet"):
            # Extract version from filename: normalized_v1.parquet -> v1
            version = file.stem.replace("normalized_", "")
            versions.append(version)
        
        # Fallback to JSON for backward compatibility
        if not versions:
            for file in normalized_dir.glob("normalized_*.json"):
                version = file.stem.replace("normalized_", "")
                versions.append(version)
        
        return sorted(versions, key=lambda v: int(v[1:]) if v.startswith("v") and v[1:].isdigit() else 0)
