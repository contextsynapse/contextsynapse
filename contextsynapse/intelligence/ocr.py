"""OCR utility — extract text from scanned documents and images.

Uses pytesseract when available, with graceful fallback.

Usage:
    from contextsynapse.intelligence.ocr import extract_text_from_image

    text = extract_text_from_image("/path/to/scan.png")
    # → "Quarterly Revenue Report..."
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

_AVAILABLE = None


def is_available() -> bool:
    """Check if OCR (pytesseract) is available."""
    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _AVAILABLE = True
        except Exception:
            _AVAILABLE = False
    return _AVAILABLE


def extract_text_from_image(image_path: str, lang: str = "eng") -> str:
    """Extract text from an image using OCR.

    Args:
        image_path: Path to image file (PNG, JPG, TIFF, BMP)
        lang: Tesseract language code (default: "eng")

    Returns extracted text or empty string if OCR unavailable.
    """
    if not is_available():
        logger.warning("[OCR] pytesseract not available. Install: pip install pytesseract")
        return ""

    try:
        import pytesseract
        from PIL import Image

        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang=lang)
        logger.info("[OCR] Extracted %d chars from %s", len(text), image_path)
        return text.strip()
    except ImportError:
        logger.warning("[OCR] PIL not available. Install: pip install Pillow")
        return ""
    except Exception as exc:
        logger.warning("[OCR] Failed for %s: %s", image_path, exc)
        return ""


def extract_text_from_pdf_images(pdf_path: str, lang: str = "eng") -> str:
    """Extract text from images embedded in a PDF (scanned PDF).

    Uses PyMuPDF to extract images, then OCR each one.
    """
    if not is_available():
        return ""

    try:
        import fitz  # PyMuPDF
        import pytesseract
        from PIL import Image
        import io

        doc = fitz.open(pdf_path)
        all_text = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            images = page.get_images(full=True)

            for img_idx, img_info in enumerate(images):
                xref = img_info[0]
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]

                img = Image.open(io.BytesIO(image_bytes))
                text = pytesseract.image_to_string(img, lang=lang)
                if text.strip():
                    all_text.append(f"[Page {page_num + 1}, Image {img_idx + 1}]\n{text.strip()}")

        doc.close()
        result = "\n\n".join(all_text)
        logger.info("[OCR] Extracted %d chars from %d pages of %s", len(result), len(doc), pdf_path)
        return result

    except ImportError as exc:
        logger.warning("[OCR] Missing dependency for PDF OCR: %s", exc)
        return ""
    except Exception as exc:
        logger.warning("[OCR] PDF OCR failed for %s: %s", pdf_path, exc)
        return ""
