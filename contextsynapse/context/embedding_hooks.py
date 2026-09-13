"""
Embedding Hooks
================
Pluggable hooks that auto-embed content into the vector store during ingestion.

Handles:
- Text → embed directly
- Images → OCR text extraction (pytesseract) + optional CLIP embeddings
- Audio → transcription placeholder (whisper)
- Other blobs → skip with warning

All dependencies are optional; missing libs degrade gracefully.

Usage::

    from contextsynapse.context.embedding_hooks import EmbeddingHooks

    hooks = EmbeddingHooks(vector_store=my_vector_store)
    hooks.on_ingest(session_id, node_id, "some text", data_type="unstructured")
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# --- Optional dependency probing ---

_ocr_available = False
try:
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore
    _ocr_available = True
except ImportError:
    pass

_clip_available = False
_clip_model = None
_clip_preprocess = None
try:
    import open_clip  # type: ignore
    _clip_available = True
except ImportError:
    try:
        import clip as _clip_mod  # type: ignore
        _clip_available = True
    except ImportError:
        pass

_whisper_available = False
try:
    import whisper  # type: ignore
    _whisper_available = True
except ImportError:
    pass


class EmbeddingHooks:
    """
    Auto-embed content after ingestion into the session vector store.

    Instantiate once per app and pass to ``UnifiedIngestor``.
    """

    def __init__(
        self,
        vector_store=None,
        enable_ocr: bool = True,
        enable_clip: bool = True,
        enable_whisper: bool = True,
    ):
        self._vector_store = vector_store
        self._enable_ocr = enable_ocr and _ocr_available
        self._enable_clip = enable_clip and _clip_available
        self._enable_whisper = enable_whisper and _whisper_available

    @property
    def available(self) -> bool:
        """Whether the vector store backend is ready."""
        return self._vector_store is not None and getattr(self._vector_store, "available", False)

    @property
    def capabilities(self) -> Dict[str, bool]:
        """Report which embedding capabilities are available."""
        return {
            "vector_store": self.available,
            "ocr": self._enable_ocr,
            "clip": self._enable_clip,
            "whisper": self._enable_whisper,
        }

    # ------------------------------------------------------------------
    # Main hook — called after ingest
    # ------------------------------------------------------------------

    def on_ingest(
        self,
        session_id: str,
        node_id: str,
        data: Any,
        data_type: str,
        mime_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Auto-embed ingested content based on its type.

        Returns a dict with embedding status info.
        """
        result: Dict[str, Any] = {
            "embedded": False,
            "extracted_text": None,
            "method": None,
        }

        if not self.available:
            return result

        meta = metadata or {}

        if data_type in ("unstructured", "semi_structured"):
            text = str(data) if not isinstance(data, str) else data
            if text.strip():
                ok = self.embed_text(session_id, node_id, text, meta)
                result["embedded"] = ok
                result["method"] = "text"

        elif data_type == "structured":
            # Convert structured data to text representation for embedding
            text = self._structured_to_text(data)
            if text:
                ok = self.embed_text(session_id, node_id, text, meta)
                result["embedded"] = ok
                result["method"] = "structured_text"

        elif data_type == "binary":
            if isinstance(data, bytes):
                blob_result = self.extract_and_embed_blob(
                    session_id, node_id, data, mime_type or "", meta,
                )
                result.update(blob_result)

        return result

    # ------------------------------------------------------------------
    # Text embedding
    # ------------------------------------------------------------------

    def embed_text(
        self,
        session_id: str,
        node_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Embed text content into the session vector store."""
        if not self.available:
            return False
        try:
            self._vector_store.add_text(
                session_id=session_id,
                text=text,
                node_id=node_id,
                metadata=metadata or {},
            )
            return True
        except Exception as e:
            logger.warning(f"Failed to embed text for node {node_id}: {e}")
            return False

    # ------------------------------------------------------------------
    # Blob processing
    # ------------------------------------------------------------------

    def extract_and_embed_blob(
        self,
        session_id: str,
        node_id: str,
        data: bytes,
        mime_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Extract text from a blob and embed it.

        Strategy by MIME type:
        - image/* → OCR (pytesseract)
        - audio/* → transcription (whisper)
        - text/* → decode + embed
        - application/pdf → basic text extraction
        """
        result: Dict[str, Any] = {
            "embedded": False,
            "extracted_text": None,
            "method": None,
        }

        if mime_type.startswith("image/"):
            text = self._extract_text_from_image(data)
            if text:
                result["extracted_text"] = text
                result["method"] = "ocr"
                ok = self.embed_text(session_id, node_id, text, metadata)
                result["embedded"] = ok

        elif mime_type.startswith("audio/"):
            text = self._transcribe_audio(data)
            if text:
                result["extracted_text"] = text
                result["method"] = "whisper"
                ok = self.embed_text(session_id, node_id, text, metadata)
                result["embedded"] = ok

        elif mime_type.startswith("text/"):
            try:
                text = data.decode("utf-8", errors="replace")
                if text.strip():
                    result["extracted_text"] = text[:500]  # preview
                    result["method"] = "text_decode"
                    ok = self.embed_text(session_id, node_id, text, metadata)
                    result["embedded"] = ok
            except Exception:
                pass

        elif mime_type == "application/pdf":
            text = self._extract_text_from_pdf(data)
            if text:
                result["extracted_text"] = text[:500]
                result["method"] = "pdf"
                ok = self.embed_text(session_id, node_id, text, metadata)
                result["embedded"] = ok

        else:
            logger.debug(f"No embedding strategy for MIME type: {mime_type}")

        return result

    # ------------------------------------------------------------------
    # Image text extraction
    # ------------------------------------------------------------------

    def _extract_text_from_image(self, data: bytes) -> Optional[str]:
        """Extract text from image bytes using OCR (pytesseract)."""
        if not self._enable_ocr:
            logger.debug("OCR not available (pytesseract/PIL not installed)")
            return None
        try:
            image = Image.open(io.BytesIO(data))
            text = pytesseract.image_to_string(image).strip()
            return text if text else None
        except Exception as e:
            logger.warning(f"OCR extraction failed: {e}")
            return None

    # ------------------------------------------------------------------
    # Audio transcription
    # ------------------------------------------------------------------

    def _transcribe_audio(self, data: bytes) -> Optional[str]:
        """Transcribe audio bytes using whisper."""
        if not self._enable_whisper:
            logger.debug("Whisper not available")
            return None
        try:
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                f.write(data)
                tmp_path = f.name
            try:
                model = whisper.load_model("base")
                result = model.transcribe(tmp_path)
                return result.get("text", "").strip() or None
            finally:
                os.unlink(tmp_path)
        except Exception as e:
            logger.warning(f"Audio transcription failed: {e}")
            return None

    # ------------------------------------------------------------------
    # PDF text extraction
    # ------------------------------------------------------------------

    def _extract_text_from_pdf(self, data: bytes) -> Optional[str]:
        """Extract text from PDF bytes."""
        # Try pdfplumber first
        try:
            import pdfplumber  # type: ignore
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                texts = [page.extract_text() or "" for page in pdf.pages]
            text = "\n".join(texts).strip()
            return text if text else None
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"pdfplumber extraction failed: {e}")

        # Fallback to PyPDF2
        try:
            from PyPDF2 import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(data))
            texts = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(texts).strip()
            return text if text else None
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"PyPDF2 extraction failed: {e}")

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _structured_to_text(data: Any) -> Optional[str]:
        """Convert structured data (dict/list) to a text representation for embedding."""
        if isinstance(data, dict):
            parts = []
            for k, v in data.items():
                if not str(k).startswith("_"):
                    parts.append(f"{k}: {v}")
            return "; ".join(parts) if parts else None
        elif isinstance(data, list):
            texts = []
            for item in data[:50]:  # cap to avoid huge embeddings
                if isinstance(item, dict):
                    t = EmbeddingHooks._structured_to_text(item)
                    if t:
                        texts.append(t)
                else:
                    texts.append(str(item))
            return "\n".join(texts) if texts else None
        return str(data) if data else None
