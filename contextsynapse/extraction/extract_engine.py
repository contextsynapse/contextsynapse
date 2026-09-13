"""
Extraction Engine

Orchestrates extraction runs, versioning, and normalization.
"""

from typing import Optional, Dict, Any
import logging
from pathlib import Path

from .config import ExtractionConfig, ExtractionMode
from .registry import ExtractorRegistry, BaseExtractor
from .file_manager import FileManager
from .version_manager import VersionManager
from .normalizer import Normalizer, NormalizedDocument
from .id_generator import IDGenerator, DeduplicationManager

logger = logging.getLogger(__name__)


class ExtractionEngine:
    """
    Main extraction engine that orchestrates the extraction process.
    """
    
    def __init__(self, config: ExtractionConfig):
        """
        Initialize extraction engine.
        
        Args:
            config: Extraction configuration
        """
        self.config = config
        self.file_manager: Optional[FileManager] = None
        self.version_manager: Optional[VersionManager] = None
        self.normalizer: Optional[Normalizer] = None
        self.extractor: Optional[BaseExtractor] = None
    
    def _initialize_components(self, document_id: str) -> None:
        """Initialize file manager, version manager, and normalizer."""
        self.file_manager = FileManager(document_id, self.config.output_dir, namespace=self.config.namespace)
        self.version_manager = VersionManager(self.file_manager)
        self.normalizer = Normalizer(self.file_manager)
    
    def _get_or_create_extractor(self) -> BaseExtractor:
        """Get or create extractor instance."""
        if self.extractor:
            return self.extractor
        
        # Get extractor
        if self.config.reader == "AUTO":
            extractor = ExtractorRegistry.auto_detect(self.config.file_path, self.config)
        else:
            extractor = ExtractorRegistry.create(self.config.reader, self.config)
        
        if not extractor:
            raise ValueError(f"No extractor found for reader: {self.config.reader}")
        
        self.extractor = extractor
        return extractor
    
    def _determine_document_id(self, file_hash: str) -> str:
        """
        Determine document ID from config or generate one.
        
        Strategy:
        1. Use provided document_id if available
        2. Check for existing document by hash (deduplication)
        3. Generate new ID using IDGenerator
        
        Args:
            file_hash: File hash for deduplication
            
        Returns:
            Document ID
        """
        if self.config.document_id:
            return self.config.document_id
        
        # Check for existing document (deduplication)
        dedup_manager = DeduplicationManager(
            namespace=self.config.namespace,
            base_dir=self.config.output_dir
        )
        
        existing = dedup_manager.check_duplicate(self.config.file_path, file_hash)
        if existing:
            logger.info(f"Found existing document: {existing['document_id']} (hash: {file_hash[:16]}...)")
            return existing['document_id']
        
        # Generate new document ID
        document_id = IDGenerator.generate_document_id(
            file_path=self.config.file_path,
            file_hash=file_hash,
            namespace=self.config.namespace,
            use_hash=True  # Deterministic ID based on hash
        )
        
        logger.info(f"Generated new document ID: {document_id}")
        return document_id
    
    def run(self) -> str:
        """
        Run extraction process.
        
        Returns:
            Document ID
        """
        # Initialize file manager first (needed for version manager)
        # Use a temporary document_id for initialization
        # Use namespace-aware path even for temp files
        temp_document_id = "temp_" + str(hash(self.config.file_path))
        self.file_manager = FileManager(temp_document_id, self.config.output_dir, namespace=self.config.namespace)
        self.version_manager = VersionManager(self.file_manager)
        
        # Compute file hash first (needed for deduplication and ID generation)
        file_hash = self.version_manager.get_file_hash(self.config.file_path)
        logger.debug(f"File hash: {file_hash}")
        
        # Determine document ID (with deduplication check)
        document_id = self._determine_document_id(file_hash)
        logger.info(f"Starting extraction for document: {document_id}")
        
        # Initialize components with correct document_id
        self._initialize_components(document_id)
        
        # Get extractor
        extractor = self._get_or_create_extractor()
        extractor_version = extractor.extractor_version if hasattr(extractor, 'extractor_version') else "unknown"
        
        # Determine requested pages
        requested_pages = self._get_requested_pages()
        
        # Check for existing version
        existing_version = self.version_manager.check_existing_version(
            file_hash=file_hash,
            extractor_version=extractor_version,
            pages_mode=self.config.pages_mode.value,
            requested_pages=requested_pages
        )
        
        if existing_version:
            logger.info(f"Found existing extraction version: {existing_version}")
            # Check if we need to merge new pages
            if self.config.pages_mode != ExtractionMode.FULL and requested_pages:
                existing_info = self.version_manager.get_version_info(existing_version)
                existing_pages = existing_info.get("extracted_pages", [])
                new_pages = [p for p in requested_pages if p not in existing_pages]
                
                if new_pages:
                    logger.info(f"Merging new pages: {new_pages}")
                    return self._merge_extraction(existing_version, new_pages, extractor, file_hash, extractor_version)
                else:
                    logger.info("All requested pages already extracted")
                    return document_id
            else:
                logger.info("Exact extraction already exists, skipping")
                return document_id
        
        # Run extraction
        logger.info("Running extraction...")
        # #region agent log
        import json
        import os
        log_path = r"c:\qgraph\qgraph-app\.cursor\debug.log"
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"extraction","hypothesisId":"A","location":"extract_engine.py:161","message":"Starting extraction","data":{"file_path":self.config.file_path,"reader":self.config.reader,"extractor_version":extractor_version},"timestamp":int(__import__('time').time()*1000)})+"\n")
        except: pass
        # #endregion
        extraction_result = extractor.extract(self.config.file_path)
        # #region agent log
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"extraction","hypothesisId":"A","location":"extract_engine.py:163","message":"Extraction result","data":{"pages_count":len(extraction_result.pages),"tables_count":len(extraction_result.tables),"images_count":len(extraction_result.images),"has_content":extraction_result.has_content()},"timestamp":int(__import__('time').time()*1000)})+"\n")
        except: pass
        # #endregion
        
        if not extraction_result.has_content():
            logger.warning("Extraction produced no content")
            # #region agent log
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"extraction","hypothesisId":"A","location":"extract_engine.py:170","message":"No content extracted","data":{"errors":extraction_result.errors},"timestamp":int(__import__('time').time()*1000)})+"\n")
            except: pass
            # #endregion
            raise ValueError("Extraction produced no content")
        
        # Store extracted assets
        self._store_extracted_assets(extraction_result)
        
        # Save manifest
        manifest = self._create_manifest(extraction_result, extractor_version, document_id)
        self.file_manager.save_manifest(manifest)
        
        # Normalize
        normalized_doc = self.normalizer.normalize(
            extraction_result=extraction_result,
            config=self.config,
            file_hash=file_hash,
            extractor_version=extractor_version
        )
        
        # Post-process normalized document (if enabled)
        if hasattr(self.config, 'post_process') and self.config.post_process:
            try:
                from contextsynapse.aiql.engine.postprocessing import DocumentPostProcessor, PostProcessingConfig
                
                # Get post-processing config from extraction config
                post_config = None
                if hasattr(self.config, 'post_processing_config'):
                    if isinstance(self.config.post_processing_config, dict):
                        post_config = PostProcessingConfig.from_dict(self.config.post_processing_config)
                    elif isinstance(self.config.post_processing_config, PostProcessingConfig):
                        post_config = self.config.post_processing_config
                
                # Create post-processor
                post_processor = DocumentPostProcessor(post_config)
                
                # Process normalized document
                normalized_dict = normalized_doc.to_dict()
                cleaned_dict = post_processor.process(normalized_dict)
                
                # Update normalized document
                normalized_doc = type(normalized_doc)(**cleaned_dict)
                
                logger.info(f"Post-processing applied to document: {normalized_doc.document_id}")
            except Exception as e:
                logger.warning(f"Post-processing failed, continuing without it: {e}")
        
        # Determine version
        version = self.version_manager.get_next_version()
        normalized_doc.version = f"normalized_{version}"
        
        # Save normalized document to both Parquet and JSON formats
        namespace = self.config.namespace if hasattr(self.config, 'namespace') and self.config.namespace else 'default'
        normalized_path = self.file_manager.save_normalized(
            normalized_doc.to_dict(),
            version=version,
            namespace=namespace,
            use_parquet=True
        )
        
        # Archive previous version if exists
        existing_versions = self.version_manager.list_versions()
        if existing_versions:
            # Archive the most recent one
            self.version_manager.archive_version(existing_versions[-1])
        
        # Register extraction
        extracted_pages = [p.get("page_no", i+1) for i, p in enumerate(extraction_result.pages)]
        self.version_manager.register_extraction(
            version=version,
            file_hash=file_hash,
            extractor_version=extractor_version,
            pages_mode=self.config.pages_mode.value,
            extracted_pages=extracted_pages,
            normalized_file=str(normalized_path)
        )
        
        # Update deduplication manager with normalized version
        dedup_manager = DeduplicationManager(
            namespace=self.config.namespace if hasattr(self.config, 'namespace') and self.config.namespace else None,
            base_dir=self.config.output_dir
        )
        dedup_info = dedup_manager.register_document(
            file_path=self.config.file_path,
            document_id=document_id,
            file_hash=file_hash,
            normalized_version=version
        )
        
        logger.info(f"Extraction complete. Document ID: {document_id}, Version: {version}")
        logger.info(f"Deduplication: {dedup_info['status']}, extraction count: {dedup_info.get('extraction_count', 1)}")
        return document_id
    
    def _get_requested_pages(self) -> Optional[list]:
        """Get list of requested page numbers."""
        if self.config.pages_mode == ExtractionMode.FULL:
            return None
        elif self.config.pages_mode == ExtractionMode.RANGE:
            if self.config.pages_range:
                start, end = self.config.pages_range
                return list(range(start, end + 1))
        elif self.config.pages_mode == ExtractionMode.LIST:
            return self.config.pages_list
        return None
    
    def _store_extracted_assets(self, result) -> None:
        """Store extracted assets using file manager."""
        # Store pages
        for i, page_data in enumerate(result.pages, 1):
            page_no = page_data.get("page_no", i)
            text = page_data.get("text", page_data.get("content", ""))
            self.file_manager.store_page(page_no, text)
        
        # Store tables
        for i, table_data in enumerate(result.tables, 1):
            table_id = table_data.get("id", f"tbl_{i:02d}")
            table_rows = table_data.get("data", table_data.get("rows", []))
            self.file_manager.store_table(table_id, table_rows)
        
        # Store images
        for i, image_data in enumerate(result.images, 1):
            image_id = image_data.get("id", f"img_{i:02d}")
            image_path = image_data.get("path", image_data.get("filename", ""))
            if image_path and Path(image_path).exists():
                self.file_manager.store_image(image_id, image_path)
        
        # Store audio
        for i, audio_data in enumerate(result.audio, 1):
            audio_id = audio_data.get("id", f"aud_{i:02d}")
            audio_path = audio_data.get("path", audio_data.get("filename", ""))
            if audio_path and Path(audio_path).exists():
                self.file_manager.store_audio(audio_id, audio_path)
        
        # Store video frames
        for i, video_data in enumerate(result.video, 1):
            video_id = video_data.get("id", f"vid_{i:02d}")
            keyframes = video_data.get("keyframes", [])
            for frame_no, frame_path in enumerate(keyframes, 1):
                if Path(frame_path).exists():
                    self.file_manager.store_video_frame(frame_no, frame_path)
    
    def _create_manifest(self, result, extractor_version: str, document_id: str) -> Dict[str, Any]:
        """Create extraction manifest."""
        from datetime import datetime
        
        manifest = {
            "document_id": document_id,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "extractor_version": extractor_version,
            "assets": {
                "pages": [
                    f"pages/page_{page_data.get('page_no', i+1):03d}.txt"
                    for i, page_data in enumerate(result.pages)
                ],
                "tables": [
                    f"tables/tbl_{table_data.get('id', f'tbl_{i+1:02d}')}.csv"
                    for i, table_data in enumerate(result.tables)
                ],
                "images": [
                    f"images/img_{image_data.get('id', f'img_{i+1:02d}')}.png"
                    for i, image_data in enumerate(result.images)
                ],
                "audio": [
                    f"audio/audio_{audio_data.get('id', f'aud_{i+1:02d}')}.wav"
                    for i, audio_data in enumerate(result.audio)
                ],
                "video_frames": [],
                "web": [],
            }
        }
        
        return manifest
    
    def _merge_extraction(
        self,
        existing_version: str,
        new_pages: list,
        extractor: BaseExtractor,
        file_hash: str,
        extractor_version: str
    ) -> str:
        """Merge new pages into existing extraction."""
        logger.info(f"Merging new pages {new_pages} into version {existing_version}")
        
        # Extract only new pages
        # Note: This requires extractor to support page filtering
        # For now, we'll extract all and filter
        extraction_result = extractor.extract(self.config.file_path)
        
        # Filter to new pages only
        filtered_pages = [
            p for p in extraction_result.pages
            if p.get("page_no") in new_pages
        ]
        extraction_result.pages = filtered_pages
        
        # Store new assets
        self._store_extracted_assets(extraction_result)
        
        # Merge with existing
        merged_doc = self.version_manager.merge_pages(
            existing_version=existing_version,
            new_pages=new_pages,
            new_extraction_result={
                "pages": [p for p in extraction_result.pages]
            }
        )
        
        # Create new version
        new_version = self.version_manager.get_next_version()
        merged_doc["version"] = f"normalized_{new_version}"
        
        # Save merged document (with hybrid storage if namespace available)
        namespace = self.config.namespace if hasattr(self.config, 'namespace') and self.config.namespace else 'default'
        normalized_path = self.file_manager.save_normalized(
            merged_doc,
            version=new_version,
            namespace=namespace,
            use_hybrid_storage=True
        )
        
        # Update version info
        existing_info = self.version_manager.get_version_info(existing_version)
        all_pages = sorted(set(existing_info.get("extracted_pages", []) + new_pages))
        
        self.version_manager.register_extraction(
            version=new_version,
            file_hash=file_hash,
            extractor_version=extractor_version,
            pages_mode=self.config.pages_mode.value,
            extracted_pages=all_pages,
            normalized_file=str(normalized_path)
        )
        
        # document_id should already be available from the existing version
        # Get it from the existing version info
        existing_info = self.version_manager.get_version_info(existing_version)
        document_id = existing_info.get('document_id') if existing_info else None
        if not document_id:
            # Fallback: compute from file_hash
            document_id = self._determine_document_id(file_hash)
        logger.info(f"Merge complete. New version: {new_version}")
        return document_id


def run_extraction(config: ExtractionConfig) -> str:
    """
    Convenience function to run extraction.
    
    Args:
        config: Extraction configuration
        
    Returns:
        Document ID
    """
    engine = ExtractionEngine(config)
    return engine.run()
