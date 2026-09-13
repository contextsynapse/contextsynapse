"""
Normalizer

Builds universal normalized JSON from extraction results.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from pathlib import Path
import logging

from .hierarchy import HierarchyBuilder
from .file_manager import FileManager
from .semantic_normalizer import SemanticNormalizer

logger = logging.getLogger(__name__)


@dataclass
class NormalizedDocument:
    """
    Universal normalized document structure.
    
    This is the core data model for Stage-1 output.
    """
    document_id: str
    version: str
    aiql_schema_version: str = "v1.0-core"
    
    source: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    extraction_scope: Dict[str, Any] = field(default_factory=dict)
    content: Dict[str, Any] = field(default_factory=dict)
    document_hierarchy: Dict[str, Any] = field(default_factory=dict)
    hierarchy_index: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    fast_index: Dict[str, Any] = field(default_factory=dict)
    global_text_cache: str = ""
    status: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "document_id": self.document_id,
            "version": self.version,
            "aiql_schema_version": self.aiql_schema_version,
            "source": self.source,
            "metadata": self.metadata,
            "extraction_scope": self.extraction_scope,
            "content": self.content,
            "document_hierarchy": self.document_hierarchy,
            "hierarchy_index": self.hierarchy_index,
            "fast_index": self.fast_index,
            "global_text_cache": self.global_text_cache,
            "status": self.status,
        }


class Normalizer:
    """
    Normalizes extraction results into universal JSON format.
    """
    
    def __init__(self, file_manager: FileManager, enable_semantic_normalization: bool = True):
        """
        Initialize normalizer.
        
        Args:
            file_manager: File manager instance
            enable_semantic_normalization: Whether to apply semantic normalization (paragraph rebuilding)
        """
        self.file_manager = file_manager
        self.hierarchy_builder = HierarchyBuilder()
        self.enable_semantic_normalization = enable_semantic_normalization
        self.semantic_normalizer = SemanticNormalizer(enable_toc_detection=True) if enable_semantic_normalization else None
    
    def normalize(
        self,
        extraction_result: "ExtractionResult",
        config: "ExtractionConfig",
        file_hash: str,
        extractor_version: str
    ) -> NormalizedDocument:
        """
        Normalize extraction result into universal JSON.
        
        Args:
            extraction_result: Result from extractor
            config: Extraction configuration
            file_hash: SHA256 hash of source file
            extractor_version: Version of extractor used
            
        Returns:
            NormalizedDocument instance
        """
        # Determine document ID
        document_id = config.document_id or self._generate_document_id(config.file_path)
        
        # Build source info
        source = {
            "format": self._detect_format(config.file_path),
            "file_name": Path(config.file_path).name,
            "file_path": config.file_path,
            "file_hash": file_hash,
            "extractor_version": extractor_version,
            "ingested_at": datetime.utcnow().isoformat() + "Z",
        }
        
        # Build metadata
        metadata = extraction_result.metadata.copy()
        if "page_count" not in metadata:
            metadata["page_count"] = len(extraction_result.pages)
        
        # Build extraction scope
        extraction_scope = {
            "mode": config.pages_mode.value,
            "requested_pages": self._get_requested_pages(config),
            "original_page_count": metadata.get("page_count", len(extraction_result.pages)),
            "total_pages_extracted": len(extraction_result.pages),
        }
        
        # Build content
        # #region agent log
        import json
        import os
        log_path = r"c:\qgraph\qgraph-app\.cursor\debug.log"
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"normalization","hypothesisId":"D","location":"normalizer.py:120","message":"Building content","data":{"pages_count":len(extraction_result.pages),"tables_count":len(extraction_result.tables),"images_count":len(extraction_result.images)},"timestamp":int(__import__('time').time()*1000)})+"\n")
        except: pass
        # #endregion
        content = self._build_content(extraction_result, config)
        # #region agent log
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"normalization","hypothesisId":"D","location":"normalizer.py:125","message":"Content built","data":{"pages_in_content":len(content.get("pages",[])),"tables_in_content":len(content.get("tables",[])),"images_in_content":len(content.get("images",[]))},"timestamp":int(__import__('time').time()*1000)})+"\n")
        except: pass
        # #endregion
        
        # Build hierarchy
        document_hierarchy = self.hierarchy_builder.build_document_hierarchy(content["pages"])
        hierarchy_index = self.hierarchy_builder.get_hierarchy_index()
        
        # Build fast index
        fast_index = self._build_fast_index(content)
        
        # Build global text cache (OPTIONAL - can cause OOM for large documents)
        # Only build if explicitly requested or for small documents
        # For large documents, use content.pages[].flat_text directly instead
        page_count = len(content.get("pages", []))
        if page_count <= 50:  # Only build for small documents
            global_text_cache = self._build_global_text_cache(content["pages"])
        else:
            # For large documents, use empty string and generate on-demand if needed
            global_text_cache = ""
            logger.info(f"Skipping global_text_cache for large document ({page_count} pages) to save memory. Use content.pages[].flat_text directly.")
        
        # Build status
        status = {
            "normalized": True,
            "ready_for_chunking": True,
            "graph_links_created": False,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "last_modified": datetime.utcnow().isoformat() + "Z",
        }
        
        return NormalizedDocument(
            document_id=document_id,
            version="normalized_v1",  # Will be updated by version manager
            source=source,
            metadata=metadata,
            extraction_scope=extraction_scope,
            content=content,
            document_hierarchy=document_hierarchy,
            hierarchy_index=hierarchy_index,
            fast_index=fast_index,
            global_text_cache=global_text_cache,
            status=status,
        )
    
    def _generate_document_id(self, file_path: str) -> str:
        """Generate document ID from file path."""
        # Use hash of file path + name for deterministic ID
        path_str = str(Path(file_path).absolute())
        hash_obj = hashlib.sha256(path_str.encode())
        return f"aiql_{hash_obj.hexdigest()[:8]}"
    
    def _detect_format(self, file_path: str) -> str:
        """Detect file format from extension."""
        ext = Path(file_path).suffix.lower()
        format_map = {
            ".pdf": "pdf",
            ".docx": "docx",
            ".pptx": "pptx",
            ".csv": "csv",
            ".txt": "txt",
            ".html": "html",
            ".htm": "html",
            ".png": "image",
            ".jpg": "image",
            ".jpeg": "image",
            ".mp3": "audio",
            ".wav": "audio",
            ".mp4": "video",
            ".avi": "video",
        }
        return format_map.get(ext, "unknown")
    
    def _get_requested_pages(self, config: "ExtractionConfig") -> List[int]:
        """Get list of requested page numbers."""
        if config.pages_mode.value == "full":
            return []  # Empty means all pages
        elif config.pages_mode.value == "range":
            if config.pages_range:
                start, end = config.pages_range
                return list(range(start, end + 1))
        elif config.pages_mode.value == "list":
            return config.pages_list or []
        return []
    
    def _build_content(self, result: "ExtractionResult", config: "ExtractionConfig") -> Dict[str, Any]:
        """Build content section from extraction result."""
        # Build pages
        pages = []
        for i, page_data in enumerate(result.pages, 1):
            page = self._build_page(page_data, i)
            pages.append(page)
        
        # Build tables
        tables = []
        for i, table_data in enumerate(result.tables, 1):
            table = self._build_table(table_data, i)
            tables.append(table)
        
        # Build images
        images = []
        for i, image_data in enumerate(result.images, 1):
            image = self._build_image(image_data, i)
            images.append(image)
        
        # Build web content
        web = []
        for i, web_data in enumerate(result.web, 1):
            web_item = self._build_web(web_data, i)
            web.append(web_item)
        
        # Build audio
        audio = []
        for i, audio_data in enumerate(result.audio, 1):
            audio_item = self._build_audio(audio_data, i)
            audio.append(audio_item)
        
        # Build video
        video = []
        for i, video_data in enumerate(result.video, 1):
            video_item = self._build_video(video_data, i)
            video.append(video_item)
        
        return {
            "pages": pages,
            "tables": tables,
            "images": images,
            "web": web,
            "audio": audio,
            "video": video,
            "extensions": {},  # For future modalities
        }
    
    def _build_page(self, page_data: Dict[str, Any], page_no: int) -> Dict[str, Any]:
        """Build normalized page structure."""
        # Extract text content
        flat_text = page_data.get("text", page_data.get("content", ""))
        
        # STAGE 1: Structural Normalization (blocks) - this is the primary representation
        normalized_blocks = self._build_normalized_blocks(page_data, page_no)
        
        # STAGE 2: Semantic Normalization (paragraph rebuild) - rebuild paragraphs from fragmented blocks
        rebuilt_paragraphs = []
        if self.enable_semantic_normalization and self.semantic_normalizer:
            rebuilt_paragraphs = self.semantic_normalizer.rebuild_paragraphs(normalized_blocks, page_no)
            logger.debug(f"Page {page_no}: Rebuilt {len(rebuilt_paragraphs)} paragraphs from {len(normalized_blocks)} blocks")
        
        # MEMORY OPTIMIZATION: Only build raw_blocks and markdown for small documents
        # For large documents, generate on-demand when needed
        # This reduces memory by ~60% for text representations
        page_text_size = len(flat_text) if isinstance(flat_text, str) else 0
        
        # Build raw blocks only if page is small (to save memory)
        raw_blocks = []
        if page_text_size < 10000:  # Only for pages < 10KB
            if flat_text:
                # Split into paragraphs for raw blocks
                paragraphs = flat_text.split("\n\n")
                for i, para in enumerate(paragraphs):
                    if para.strip():
                        raw_blocks.append({
                            "id": f"raw_{page_no}_{i+1}",
                            "type": "text",
                            "content": para.strip(),
                        })
        
        # Build markdown only if page is small (to save memory)
        markdown = ""
        if page_text_size < 10000:  # Only for pages < 10KB
            markdown = self._build_markdown(normalized_blocks)
        # For larger pages, markdown can be generated on-demand from normalized_blocks
        
        page_structure = {
            "page_no": page_no,
            "flat_text": flat_text,
            "raw_blocks": raw_blocks,  # Empty for large pages
            "normalized_blocks": normalized_blocks,  # Structural blocks (Stage 1)
            "markdown": markdown,  # Empty for large pages
            "hierarchy": {},  # Will be filled by hierarchy builder
        }
        
        # Add rebuilt paragraphs if semantic normalization was applied
        if rebuilt_paragraphs:
            page_structure["rebuilt_paragraphs"] = rebuilt_paragraphs  # Semantic paragraphs (Stage 2)
        
        return page_structure
    
    def _build_normalized_blocks(self, page_data: Dict[str, Any], page_no: int) -> List[Dict[str, Any]]:
        """Build normalized blocks with structure detection."""
        text = page_data.get("text", page_data.get("content", ""))
        blocks = page_data.get("blocks", [])
        
        if blocks:
            # Use provided blocks (with font size information)
            normalized = []
            for i, block in enumerate(blocks):
                block_id = block.get("id", f"b{page_no}_{i+1}")
                normalized_block = {
                    "id": block_id,
                    "type": block.get("type", "paragraph"),
                    "level": block.get("level"),
                    "text": block.get("text", block.get("content", "")),
                }
                # Include font size if available
                if "font_size" in block:
                    normalized_block["font_size"] = block["font_size"]
                if "font_name" in block:
                    normalized_block["font_name"] = block["font_name"]
                normalized.append(normalized_block)
            return normalized
        
        # Auto-detect structure from text
        lines = text.split("\n")
        normalized = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            
            block_id = f"b{page_no}_{i+1}"
            
            # Detect heading (simple heuristic: all caps or starts with #)
            if line.strip().startswith("#"):
                level = len(line) - len(line.lstrip("#"))
                normalized.append({
                    "id": block_id,
                    "type": "heading",
                    "level": min(level, 6),
                    "text": line.lstrip("#").strip(),
                })
            elif line.strip().isupper() and len(line.strip()) < 100:
                # Likely a heading
                normalized.append({
                    "id": block_id,
                    "type": "heading",
                    "level": 1,
                    "text": line.strip(),
                })
            else:
                # Regular paragraph
                normalized.append({
                    "id": block_id,
                    "type": "paragraph",
                    "text": line.strip(),
                })
        
        return normalized
    
    def _build_markdown(self, blocks: List[Dict[str, Any]]) -> str:
        """Build markdown representation from blocks."""
        markdown_lines = []
        for block in blocks:
            block_type = block.get("type")
            text = block.get("text", "")
            font_size = block.get("font_size")
            
            if block_type == "heading" or block_type == "subheading":
                level = block.get("level")
                # If no level specified but it's a heading, infer from font size
                if level is None and font_size:
                    if font_size >= 16:
                        level = 1
                    elif font_size >= 14:
                        level = 2
                    elif font_size >= 12:
                        level = 3
                    else:
                        level = 1
                elif level is None:
                    level = 1
                markdown_lines.append(f"{'#' * level} {text}")
            elif block_type == "paragraph":
                markdown_lines.append(text)
            else:
                markdown_lines.append(text)
        
        # Join with double newlines for proper markdown formatting
        return "\n\n".join(markdown_lines)
    
    def _build_table(self, table_data: Dict[str, Any], table_index: int) -> Dict[str, Any]:
        """Build normalized table structure."""
        table_id = table_data.get("id", f"tbl_{table_index:02d}")
        data = table_data.get("data", table_data.get("rows", []))
        headers = table_data.get("headers", data[0] if data else [])
        rows = data[1:] if data and len(data) > 1 else data
        
        # MEMORY OPTIMIZATION: Only build markdown for small tables
        # For large tables, generate on-demand when needed
        # This reduces memory by ~50% for table representations
        table_size = len(str(data))  # Approximate size
        markdown = ""
        if table_size < 50000:  # Only for tables < 50KB
            markdown = self._table_to_markdown(headers, rows)
        # For larger tables, markdown can be generated on-demand from data
        
        return {
            "id": table_id,
            "from_page": table_data.get("page_number", table_data.get("page_no", 1)),
            "data": [headers] + rows if headers else rows,
            "markdown": markdown,  # Empty for large tables
        }
    
    def _table_to_markdown(self, headers: List[str], rows: List[List[str]]) -> str:
        """Convert table to markdown format."""
        if not headers and not rows:
            return ""
        
        if not headers and rows:
            headers = [f"Column {i+1}" for i in range(len(rows[0]))]
        
        lines = []
        # Header
        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        # Separator
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        # Rows
        for row in rows:
            lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
        
        return "\n".join(lines)
    
    def _build_image(self, image_data: Dict[str, Any], image_index: int) -> Dict[str, Any]:
        """Build normalized image structure."""
        image_id = image_data.get("id", f"img_{image_index:02d}")
        path = image_data.get("path", image_data.get("filename", ""))
        if isinstance(path, Path):
            path = str(path.relative_to(self.file_manager.workspace))
        
        return {
            "id": image_id,
            "page_no": image_data.get("page_number", image_data.get("page_no", 1)),
            "path": path,
            "ocr_text": image_data.get("ocr_text", image_data.get("description", "")),
            "markdown": f"![{image_id}]({path})\n> {image_data.get('description', '')}",
        }
    
    def _build_web(self, web_data: Dict[str, Any], web_index: int) -> Dict[str, Any]:
        """Build normalized web content structure."""
        return {
            "url": web_data.get("url", ""),
            "html": web_data.get("html", ""),
            "clean_text": web_data.get("clean_text", web_data.get("text", "")),
            "markdown": web_data.get("markdown", ""),
        }
    
    def _build_audio(self, audio_data: Dict[str, Any], audio_index: int) -> Dict[str, Any]:
        """Build normalized audio structure."""
        audio_id = audio_data.get("id", f"aud_{audio_index:02d}")
        path = audio_data.get("path", audio_data.get("filename", ""))
        if isinstance(path, Path):
            path = str(path.relative_to(self.file_manager.workspace))
        
        return {
            "id": audio_id,
            "path": path,
            "duration_sec": audio_data.get("duration", audio_data.get("duration_sec")),
            "transcript": audio_data.get("transcript", ""),
            "timestamps": audio_data.get("timestamps", []),
        }
    
    def _build_video(self, video_data: Dict[str, Any], video_index: int) -> Dict[str, Any]:
        """Build normalized video structure."""
        video_id = video_data.get("id", f"vid_{video_index:02d}")
        path = video_data.get("path", video_data.get("filename", ""))
        if isinstance(path, Path):
            path = str(path.relative_to(self.file_manager.workspace))
        
        return {
            "id": video_id,
            "path": path,
            "duration_sec": video_data.get("duration", video_data.get("duration_sec")),
            "transcript": video_data.get("transcript", ""),
            "keyframes": video_data.get("keyframes", []),
            "scene_segments": video_data.get("scene_segments", []),
        }
    
    def _build_fast_index(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """Build fast index for quick lookups."""
        index = {
            "pages": {},
            "tables": {},
            "images": {},
        }
        
        # Index pages
        for i, page in enumerate(content.get("pages", [])):
            page_no = page.get("page_no", i + 1)
            block_ids = [b.get("id") for b in page.get("normalized_blocks", [])]
            index["pages"][str(page_no)] = {
                "offset": i,
                "block_ids": block_ids,
                "markdown_ptr": f"content.pages[{i}].markdown",
            }
        
        # Index tables
        for i, table in enumerate(content.get("tables", [])):
            table_id = table.get("id", f"tbl_{i+1}")
            index["tables"][table_id] = f"content.tables[{i}]"
        
        # Index images
        for i, image in enumerate(content.get("images", [])):
            image_id = image.get("id", f"img_{i+1}")
            index["images"][image_id] = f"content.images[{i}]"
        
        return index
    
    def _build_global_text_cache(self, pages: List[Dict[str, Any]]) -> str:
        """Build global text cache from all pages."""
        text_parts = []
        for page in pages:
            page_no = page.get("page_no", 0)
            flat_text = page.get("flat_text", "")
            text_parts.append(f"[Page {page_no}]\n{flat_text}")
        return "\n\n".join(text_parts)
    
    @staticmethod
    def generate_markdown_from_blocks(normalized_blocks: List[Dict[str, Any]]) -> str:
        """Generate markdown on-demand from normalized blocks (for pages that skipped markdown generation)."""
        markdown_lines = []
        for block in normalized_blocks:
            block_type = block.get("type")
            text = block.get("text", "")
            font_size = block.get("font_size")
            
            if block_type == "heading" or block_type == "subheading":
                level = block.get("level")
                if level is None and font_size:
                    if font_size >= 16:
                        level = 1
                    elif font_size >= 14:
                        level = 2
                    elif font_size >= 12:
                        level = 3
                    else:
                        level = 1
                elif level is None:
                    level = 1
                markdown_lines.append(f"{'#' * level} {text}")
            elif block_type == "paragraph":
                markdown_lines.append(text)
            else:
                markdown_lines.append(text)
        
        return "\n\n".join(markdown_lines)
    
    @staticmethod
    def generate_raw_blocks_from_text(flat_text: str, page_no: int) -> List[Dict[str, Any]]:
        """Generate raw blocks on-demand from flat text (for pages that skipped raw_blocks generation)."""
        raw_blocks = []
        if flat_text:
            paragraphs = flat_text.split("\n\n")
            for i, para in enumerate(paragraphs):
                if para.strip():
                    raw_blocks.append({
                        "id": f"raw_{page_no}_{i+1}",
                        "type": "text",
                        "content": para.strip(),
                    })
        return raw_blocks
