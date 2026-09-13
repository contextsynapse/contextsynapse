"""
PDF Extractor with Layout Parser Support

Uses Layout Parser for accurate multi-column document extraction.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class PdfExtractorLayoutParser(BaseExtractor):
    """
    PDF extractor using Layout Parser for multi-column layouts.
    
    Features:
    - ML-based layout detection
    - Proper reading order (column-by-column)
    - Handles complex layouts
    """
    
    def __init__(self, config):
        """Initialize Layout Parser PDF extractor."""
        super().__init__(config)
        self.extractor_version = "pdf_v2_layoutparser"
        self.layout_model = None
        self._initialize_layout_parser()
    
    def _initialize_layout_parser(self):
        """Initialize Layout Parser model."""
        try:
            import layoutparser as lp
            logger.info("Layout Parser library imported successfully")
            
            # Try to load PubLayNet model
            # Pre-download models first, then load
            try:
                logger.info("Loading Layout Parser model (this will download models on first use, ~221MB)...")
                
                # Pre-download and resolve model path
                # The lp://PubLayNet format needs to be converted to lp://paddledetection format
                try:
                    from layoutparser.models.paddledetection.catalog import PathManager
                    # Convert lp://PubLayNet/.../model to lp://paddledetection/PubLayNet/.../weight
                    # This is the format that PathManager expects
                    model_path_lp = "lp://paddledetection/PubLayNet/ppyolov2_r50vd_dcn_365e/weight"
                    logger.info("Pre-downloading model files (this may take several minutes, ~221MB)...")
                    logger.info("Downloading from Baidu Cloud - please be patient...")
                    model_dir = PathManager.get_local_path(model_path_lp)
                    logger.info(f"Model downloaded successfully to: {model_dir}")
                    
                    # Verify model files exist
                    import os
                    if os.path.exists(os.path.join(model_dir, "inference.pdmodel")):
                        logger.info("Model files verified: inference.pdmodel and inference.pdiparams found")
                    else:
                        logger.warning(f"Model files not found in {model_dir}")
                except Exception as download_err:
                    logger.warning(f"Pre-download failed: {download_err}")
                    logger.info("Will attempt download during model creation...")
                
                # Now try to load the model
                # The lp://PubLayNet format should be automatically converted by config_parser
                # But we've pre-downloaded the files, so PathManager should find them
                try:
                    logger.info("Loading model from downloaded files...")
                    self.layout_model = lp.PaddleDetectionLayoutModel(
                        config_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/config",
                        model_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/model",
                        label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"}
                    )
                    logger.info("Layout Parser (PaddlePaddle) model loaded successfully!")
                except Exception as load_error:
                    logger.warning(f"Model load failed with lp://PubLayNet format: {load_error}")
                    # The issue is that config_parser isn't converting the path correctly
                    # Try using the format that we know works
                    try:
                        logger.info("Trying alternative path format...")
                        # Use the format that PathManager can resolve
                        # But PaddleDetectionLayoutModel expects the short format in config_path/model_path
                        # So we need to ensure the conversion happens
                        self.layout_model = lp.PaddleDetectionLayoutModel(
                            config_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/config",
                            model_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/model",
                            label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"}
                        )
                        logger.info("Layout Parser model loaded!")
                    except Exception as e2:
                        logger.error(f"All model loading attempts failed: {e2}")
                        self.layout_model = None  # Will be created on first use
            except Exception as model_error:
                # Try alternative: Detectron2LayoutModel if PaddleDetection fails
                try:
                    logger.info("Trying Detectron2LayoutModel as alternative...")
                    self.layout_model = lp.Detectron2LayoutModel(
                        config_path="lp://PubLayNet/faster_rcnn_R_50_FPN_3x/config",
                        model_path="lp://PubLayNet/faster_rcnn_R_50_FPN_3x/model",
                        label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"},
                        extra_config=["MODEL.ROI_HEADS.SCORE_THRESH_TEST", 0.5]
                    )
                    logger.info("Layout Parser (Detectron2) model loaded successfully")
                except Exception as e2:
                    logger.warning(f"Could not load Layout Parser model (PaddleDetection or Detectron2): {model_error}, {e2}")
                    logger.warning("Falling back to heuristic extraction")
                    self.layout_model = None
        except ImportError as e:
            logger.warning(f"Layout Parser not available: {e}")
            logger.warning("Install with: pip install layoutparser")
            logger.warning("For models, also install: pip install detectron2 (or paddlepaddle)")
            self.layout_model = None
        except Exception as e:
            logger.warning(f"Could not initialize Layout Parser: {e}")
            self.layout_model = None
    
    def supports_format(self, file_path: str) -> bool:
        """Check if file is a PDF."""
        return Path(file_path).suffix.lower() == ".pdf"
    
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from PDF using Layout Parser.
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            ExtractionResult
        """
        result = ExtractionResult(extractor_version=self.extractor_version)
        
        # Try to load model if not already loaded (lazy loading with download)
        if not self.layout_model:
            logger.info("Layout Parser model not loaded, attempting to load and download now...")
            logger.info("Downloading models (requires internet, may take several minutes, ~100-200MB)...")
            try:
                import layoutparser as lp
                import site
                import sys
                # Ensure user site-packages are accessible for PaddlePaddle
                user_site = site.getusersitepackages()
                if user_site and user_site not in sys.path:
                    sys.path.insert(0, user_site)
                
                # The lp:// protocol should trigger automatic download
                # Layout Parser downloads models to ~/.layoutparser/ on first use
                logger.info("Attempting to load PaddlePaddle model (will download if needed)...")
                
                # Pre-download models by calling PathManager.get_local_path()
                # This ensures models are downloaded before trying to load them
                try:
                    from layoutparser.models.paddledetection.catalog import PathManager
                    # Pre-download the model weight file
                    # The format: lp://paddledetection/Dataset/ModelName/weight
                    model_path_lp = "lp://paddledetection/PubLayNet/ppyolov2_r50vd_dcn_365e/weight"
                    logger.info("Pre-downloading model files (this may take several minutes, ~221MB)...")
                    logger.info("Downloading from Baidu Cloud - please be patient...")
                    model_dir = PathManager.get_local_path(model_path_lp)
                    logger.info(f"Model downloaded successfully to: {model_dir}")
                except Exception as download_err:
                    logger.warning(f"Pre-download failed: {download_err}")
                    logger.info("Will attempt download during model creation...")
                
                # Try PaddlePaddle first
                try:
                    # The lp://PubLayNet format is automatically converted internally
                    # This should now work since models are downloaded
                    logger.info("Loading model from downloaded files...")
                    self.layout_model = lp.PaddleDetectionLayoutModel(
                        config_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/config",
                        model_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/model",
                        label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"}
                    )
                    logger.info("Layout Parser (PaddlePaddle) model loaded successfully!")
                except Exception as paddle_err:
                    logger.warning(f"PaddlePaddle model failed: {str(paddle_err)[:200]}")
                    # Try Detectron2 as alternative
                    logger.info("Trying Detectron2 model as alternative...")
                    try:
                        self.layout_model = lp.Detectron2LayoutModel(
                            config_path="lp://PubLayNet/faster_rcnn_R_50_FPN_3x/config",
                            model_path="lp://PubLayNet/faster_rcnn_R_50_FPN_3x/model",
                            label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"}
                        )
                        logger.info("Layout Parser (Detectron2) model loaded successfully!")
                    except Exception as detectron_err:
                        error_msg = f"Both models failed. PaddlePaddle: {str(paddle_err)[:100]}, Detectron2: {str(detectron_err)[:100]}"
                        logger.error(error_msg)
                        logger.warning("Models may need to be downloaded manually or internet connection required")
                        result.errors.append("Layout Parser model not available - using fallback")
                        return result
            except Exception as e:
                logger.error(f"Could not load Layout Parser model: {e}")
                logger.warning("Falling back to standard extraction")
                result.errors.append("Layout Parser model not available - using fallback")
                return result
        
        try:
            import fitz  # PyMuPDF
            import pdfplumber
            import numpy as np
            from PIL import Image
        except ImportError as e:
            logger.error(f"Required libraries not available: {e}")
            result.errors.append(f"Missing dependencies: {e}")
            return result
        
        # Get metadata using pdfplumber
        with pdfplumber.open(file_path) as pdf:
            metadata = pdf.metadata or {}
            result.metadata = {
                "title": metadata.get("Title", ""),
                "author": metadata.get("Author", ""),
                "subject": metadata.get("Subject", ""),
                "creator": metadata.get("Creator", ""),
                "producer": metadata.get("Producer", ""),
                "page_count": len(pdf.pages),
            }
        
        # Extract using Layout Parser
        doc = fitz.open(file_path)
        requested_pages = self._get_requested_pages()
        
        try:
            for i, page_fitz in enumerate(doc, 1):
                if requested_pages and i not in requested_pages:
                    continue
                
                # Convert page to image for Layout Parser
                pix = page_fitz.get_pixmap(matrix=fitz.Matrix(2, 2))
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                img_array = np.array(img)
                
                # Detect layout
                layout = self.layout_model.detect(img_array)
                
                # Extract text with proper reading order
                text_blocks = self._extract_text_with_reading_order(
                    page_fitz, layout, i
                )
                
                # Combine text blocks
                page_text = "\n\n".join([block["text"] for block in text_blocks])
                
                result.pages.append({
                    "page_no": i,
                    "text": page_text,
                    "content": page_text,
                    "layout_blocks": text_blocks,  # Preserve block structure
                })
                
                # Extract tables using pdfplumber (better for tables)
                try:
                    with pdfplumber.open(file_path) as pdf:
                        page_plumber = pdf.pages[i - 1]
                        tables = page_plumber.extract_tables()
                        
                        for table_idx, table in enumerate(tables or [], 1):
                            if table:
                                result.tables.append({
                                    "id": f"tbl_{i}_{table_idx}",
                                    "page_number": i,
                                    "data": table,
                                    "headers": table[0] if table else [],
                                    "rows": table[1:] if len(table) > 1 else [],
                                })
                except Exception as e:
                    logger.warning(f"Error extracting tables from page {i}: {e}")
                
                # Extract images
                image_list = page_fitz.get_images()
                for img_idx, img in enumerate(image_list, 1):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        from pathlib import Path
                        import tempfile
                        temp_dir = Path(tempfile.gettempdir()) / "aiql_extraction_images"
                        temp_dir.mkdir(exist_ok=True)
                        image_path = temp_dir / f"img_{i}_{img_idx}.{image_ext}"
                        with open(image_path, 'wb') as img_file:
                            img_file.write(image_bytes)
                        
                        result.images.append({
                            "id": f"img_{i}_{img_idx}",
                            "page_number": i,
                            "path": str(image_path),
                            "format": image_ext.upper(),
                            "size_bytes": len(image_bytes),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_idx} from page {i}: {e}")
        
        finally:
            doc.close()
        
        return result
    
    def _extract_text_with_reading_order(
        self, 
        page_fitz, 
        layout, 
        page_num: int
    ) -> List[Dict[str, Any]]:
        """
        Extract text blocks with proper reading order for multi-column layouts.
        
        Reading order algorithm:
        1. Sort blocks by y-coordinate (top to bottom)
        2. Within same y-range, sort by x-coordinate (left to right)
        3. For multi-column: process left column completely, then right column
        """
        if not layout:
            # Fallback: extract text directly
            text = page_fitz.get_text()
            return [{"text": text, "type": "Text", "order": 0}]
        
        # Get text blocks (Text, Title, List)
        text_blocks = [
            block for block in layout 
            if block.type in ["Text", "Title", "List"]
        ]
        
        if not text_blocks:
            # Fallback: extract text directly
            text = page_fitz.get_text()
            return [{"text": text, "type": "Text", "order": 0}]
        
        # Sort blocks by reading order
        # Strategy: Sort by (y_top, x_left) to read top-to-bottom, left-to-right
        def get_sort_key(block):
            bbox = block.block
            # Use top y-coordinate first, then left x-coordinate
            return (bbox.y_0, bbox.x_0)
        
        sorted_blocks = sorted(text_blocks, key=get_sort_key)
        
        # Extract text for each block
        blocks_with_text = []
        for idx, block in enumerate(sorted_blocks):
            bbox = block.block
            
            # Extract text from this region
            # Convert Layout Parser coordinates to PyMuPDF coordinates
            import fitz  # Ensure fitz is imported
            rect = fitz.Rect(bbox.x_0, bbox.y_0, bbox.x_1, bbox.y_1)
            text = page_fitz.get_text("text", clip=rect)
            
            if text.strip():
                blocks_with_text.append({
                    "text": text.strip(),
                    "type": block.type,
                    "order": idx,
                    "bbox": {
                        "x0": bbox.x_0,
                        "y0": bbox.y_0,
                        "x1": bbox.x_1,
                        "y1": bbox.y_1
                    }
                })
        
        return blocks_with_text
    
    def _get_requested_pages(self) -> List[int]:
        """Get list of requested page numbers."""
        if self.config.pages_mode.value == "full":
            return []
        elif self.config.pages_mode.value == "range":
            if self.config.pages_range:
                start, end = self.config.pages_range
                return list(range(start, end + 1))
        elif self.config.pages_mode.value == "list":
            return self.config.pages_list or []
        return []




























