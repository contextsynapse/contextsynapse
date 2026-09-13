"""
Layout Detection Module

Detects document layout complexity using heuristic and ML-based methods.
Supports Layout Parser for accurate multi-column layout detection.
"""

from typing import Dict, Any, List, Optional, Tuple
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LayoutDetector:
    """
    Detects document layout complexity.
    
    Methods:
    1. Heuristic-based: Fast, rule-based detection
    2. ML-based: Uses Layout Parser for accurate detection
    """
    
    def __init__(self, use_ml: bool = True):
        """
        Initialize layout detector.
        
        Args:
            use_ml: Whether to use ML-based detection (Layout Parser)
        """
        self.use_ml = use_ml
        self.layout_parser = None
        self.layout_model = None
        
        if use_ml:
            self._initialize_layout_parser()
    
    def _initialize_layout_parser(self):
        """Initialize Layout Parser if available."""
        try:
            import layoutparser as lp
            self.layout_parser = lp
            logger.info("Layout Parser available for ML-based detection")
            
            # Try to load a model (will download on first use)
            try:
                # Use PubLayNet model for document layout detection
                # Note: Models download automatically on first use
                self.layout_model = lp.PaddleDetectionLayoutModel(
                    config_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/config",
                    model_path="lp://PubLayNet/ppyolov2_r50vd_dcn_365e_publaynet/model",
                    label_map={0: "Text", 1: "Title", 2: "List", 3: "Table", 4: "Figure"}
                )
                logger.info("Layout Parser model loaded successfully")
            except Exception as e:
                logger.warning(f"Could not load Layout Parser model: {e}")
                logger.warning("This is normal on first run - models will download automatically when used")
                logger.warning("Falling back to heuristic detection for now")
                self.layout_model = None
                # Don't disable ML - model will be created on first use
        except ImportError:
            logger.warning("Layout Parser not available. Install with: pip install layoutparser")
            self.use_ml = False
    
    def detect_layout_complexity(
        self, 
        file_path: str, 
        sample_pages: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Detect layout complexity of a PDF document.
        
        Args:
            file_path: Path to PDF file
            sample_pages: List of page numbers to sample (None = sample first 5 pages)
            
        Returns:
            Dict with:
                - has_complex_layout: bool
                - layout_type: str ("single_column", "multi_column", "mixed", "unknown")
                - confidence: float (0.0-1.0)
                - detection_method: str ("heuristic" or "ml")
                - details: dict with per-page analysis
        """
        if self.use_ml and self.layout_model:
            return self._detect_with_ml(file_path, sample_pages)
        else:
            return self._detect_with_heuristics(file_path, sample_pages)
    
    def _detect_with_heuristics(
        self, 
        file_path: str, 
        sample_pages: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        Heuristic-based layout detection.
        
        Analyzes:
        - Text block distribution (x-coordinates)
        - Column-like patterns
        - Text density per region
        """
        try:
            import pdfplumber
        except ImportError:
            logger.warning("pdfplumber not available for layout detection")
            return {
                "has_complex_layout": False,
                "layout_type": "unknown",
                "confidence": 0.0,
                "detection_method": "heuristic",
                "details": {}
            }
        
        if sample_pages is None:
            total_pages = self._get_page_count(file_path)
            sample_pages = list(range(1, min(6, total_pages + 1)))
        
        page_analyses = []
        multi_column_count = 0
        
        with pdfplumber.open(file_path) as pdf:
            for page_num in sample_pages:
                if page_num > len(pdf.pages):
                    continue
                
                page = pdf.pages[page_num - 1]
                analysis = self._analyze_page_heuristic(page, page_num)
                page_analyses.append(analysis)
                
                if analysis.get("is_multi_column", False):
                    multi_column_count += 1
        
        # Determine overall layout
        multi_column_ratio = multi_column_count / len(page_analyses) if page_analyses else 0
        
        if multi_column_ratio >= 0.5:
            layout_type = "multi_column"
            has_complex = True
            confidence = min(0.9, 0.5 + multi_column_ratio * 0.4)
        elif multi_column_ratio > 0:
            layout_type = "mixed"
            has_complex = True
            confidence = 0.6
        else:
            layout_type = "single_column"
            has_complex = False
            confidence = 0.7
        
        return {
            "has_complex_layout": has_complex,
            "layout_type": layout_type,
            "confidence": confidence,
            "detection_method": "heuristic",
            "details": {
                "pages_analyzed": len(page_analyses),
                "multi_column_pages": multi_column_count,
                "page_analyses": page_analyses
            }
        }
    
    def _analyze_page_heuristic(self, page, page_num: int) -> Dict[str, Any]:
        """Analyze a single page using heuristics."""
        try:
            words = page.extract_words()
            
            if not words:
                return {
                    "page_number": page_num,
                    "is_multi_column": False,
                    "confidence": 0.0,
                    "reason": "no_text"
                }
            
            # Analyze x-coordinate distribution
            x_coords = [w['x0'] for w in words]
            page_width = page.width
            
            # Divide page into left and right halves
            mid_x = page_width / 2
            left_words = [w for w in words if w['x0'] < mid_x]
            right_words = [w for w in words if w['x0'] >= mid_x]
            
            left_ratio = len(left_words) / len(words)
            right_ratio = len(right_words) / len(words)
            
            # Check for column-like distribution
            # Multi-column: text roughly balanced between left and right
            is_multi_column = (
                0.25 <= left_ratio <= 0.75 and
                0.25 <= right_ratio <= 0.75 and
                abs(left_ratio - right_ratio) < 0.4
            )
            
            confidence = 1.0 - abs(left_ratio - right_ratio) if is_multi_column else 0.0
            
            return {
                "page_number": page_num,
                "is_multi_column": is_multi_column,
                "confidence": min(confidence, 1.0),
                "left_ratio": left_ratio,
                "right_ratio": right_ratio,
                "word_count": len(words),
                "page_width": page_width
            }
            
        except Exception as e:
            logger.warning(f"Error analyzing page {page_num}: {e}")
            return {
                "page_number": page_num,
                "is_multi_column": False,
                "confidence": 0.0,
                "reason": f"error: {str(e)}"
            }
    
    def _detect_with_ml(
        self, 
        file_path: str, 
        sample_pages: Optional[List[int]] = None
    ) -> Dict[str, Any]:
        """
        ML-based layout detection using Layout Parser.
        
        This is more accurate but requires:
        - Layout Parser installed
        - Model weights downloaded
        """
        if not self.layout_model:
            return self._detect_with_heuristics(file_path, sample_pages)
        
        try:
            import fitz  # PyMuPDF
            import numpy as np
            from PIL import Image
        except ImportError:
            logger.warning("Required libraries not available for ML detection")
            return self._detect_with_heuristics(file_path, sample_pages)
        
        if sample_pages is None:
            total_pages = self._get_page_count(file_path)
            sample_pages = list(range(1, min(6, total_pages + 1)))
        
        page_analyses = []
        multi_column_count = 0
        
        doc = fitz.open(file_path)
        
        try:
            for page_num in sample_pages:
                if page_num > len(doc):
                    continue
                
                page = doc[page_num - 1]
                
                # Convert page to image
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better quality
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                img_array = np.array(img)
                
                # Detect layout
                layout = self.layout_model.detect(img_array)
                
                # Analyze layout for multi-column patterns
                analysis = self._analyze_layout_parser_result(layout, page_num)
                page_analyses.append(analysis)
                
                if analysis.get("is_multi_column", False):
                    multi_column_count += 1
        
        finally:
            doc.close()
        
        # Determine overall layout
        multi_column_ratio = multi_column_count / len(page_analyses) if page_analyses else 0
        
        if multi_column_ratio >= 0.5:
            layout_type = "multi_column"
            has_complex = True
            confidence = min(0.95, 0.7 + multi_column_ratio * 0.25)
        elif multi_column_ratio > 0:
            layout_type = "mixed"
            has_complex = True
            confidence = 0.75
        else:
            layout_type = "single_column"
            has_complex = False
            confidence = 0.8
        
        return {
            "has_complex_layout": has_complex,
            "layout_type": layout_type,
            "confidence": confidence,
            "detection_method": "ml",
            "details": {
                "pages_analyzed": len(page_analyses),
                "multi_column_pages": multi_column_count,
                "page_analyses": page_analyses
            }
        }
    
    def _analyze_layout_parser_result(self, layout, page_num: int) -> Dict[str, Any]:
        """Analyze Layout Parser detection results for multi-column patterns."""
        try:
            text_blocks = [block for block in layout if block.type in ["Text", "Title", "List"]]
            
            if len(text_blocks) < 2:
                return {
                    "page_number": page_num,
                    "is_multi_column": False,
                    "confidence": 0.0,
                    "reason": "insufficient_text_blocks"
                }
            
            # Get x-coordinates of text blocks
            x_starts = [block.block.x_0 for block in text_blocks]
            x_ends = [block.block.x_1 for block in text_blocks]
            
            # Calculate column boundaries
            all_x = sorted(x_starts + x_ends)
            
            if len(all_x) < 4:
                return {
                    "page_number": page_num,
                    "is_multi_column": False,
                    "confidence": 0.3,
                    "reason": "insufficient_blocks"
                }
            
            # Detect column clusters
            gaps = []
            for i in range(len(all_x) - 1):
                gap = all_x[i + 1] - all_x[i]
                gaps.append(gap)
            
            if not gaps:
                return {
                    "page_number": page_num,
                    "is_multi_column": False,
                    "confidence": 0.0
                }
            
            avg_gap = sum(gaps) / len(gaps)
            large_gaps = [g for g in gaps if g > avg_gap * 2]
            
            # Multi-column if we have significant gaps indicating column separation
            is_multi_column = len(large_gaps) >= 2 and len(text_blocks) >= 4
            
            confidence = min(0.9, 0.5 + (len(large_gaps) / len(text_blocks)) * 0.4)
            
            return {
                "page_number": page_num,
                "is_multi_column": is_multi_column,
                "confidence": confidence,
                "text_block_count": len(text_blocks),
                "large_gaps": len(large_gaps)
            }
            
        except Exception as e:
            logger.warning(f"Error analyzing Layout Parser result for page {page_num}: {e}")
            return {
                "page_number": page_num,
                "is_multi_column": False,
                "confidence": 0.0,
                "reason": f"error: {str(e)}"
            }
    
    def _get_page_count(self, file_path: str) -> int:
        """Get total page count of PDF."""
        try:
            import pdfplumber
            with pdfplumber.open(file_path) as pdf:
                return len(pdf.pages)
        except:
            try:
                import fitz
                doc = fitz.open(file_path)
                count = len(doc)
                doc.close()
                return count
            except:
                return 0




























