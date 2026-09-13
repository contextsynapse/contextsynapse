"""
Document Processor
==================
End-to-end pipeline: file → extract → chunk → graph nodes → permanent context.

Usage::

    from contextsynapse.context import DocumentProcessor

    proc = DocumentProcessor(session_manager, agent_registry, graph_registry)
    result = proc.process(
        session_id="...",
        agent_id="...",
        file_path="annual_report.pdf",
    )
    # result = {
    #     "document_id": "...",
    #     "source_node": "...",
    #     "document_node": "...",
    #     "chunk_nodes": ["...", ...],
    #     "table_nodes": ["...", ...],
    #     "image_nodes": ["...", ...],
    #     "edges": ["...", ...],
    #     "chunk_count": 15,
    #     "table_count": 3,
    #     "image_count": 2,
    # }

Or via the API::

    POST /context/sessions/{id}/documents
    {"file_path": "/path/to/annual_report.pdf", "agent_id": "..."}
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .agents import AgentRegistry, ProvenanceRecord

logger = logging.getLogger(__name__)

# Default chunk size ~500 tokens (~2000 chars)
DEFAULT_CHUNK_SIZE = 2000
DEFAULT_CHUNK_OVERLAP = 200


class DocumentProcessor:
    """
    Upload a document → extract → chunk → build graph → permanent context.

    Creates a knowledge graph in the session's namespace:

        Source ──SOURCE_OF──► Document
                               │
                     ┌─────────┼──────────┐
                     ▼         ▼          ▼
                   Chunk     Table      Image
                   Chunk     Table
                   Chunk
                    ...

    Each chunk stores its text as a ``content`` property.  The full document
    text is on the Document node.  All nodes are tagged with session +
    agent provenance via ``_ctx_*`` properties.
    """

    def __init__(
        self,
        session_manager,
        agent_registry: AgentRegistry,
        graph_registry=None,
        blob_store=None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self._session_mgr = session_manager
        self._agent_registry = agent_registry
        self._graph_registry = graph_registry
        self._blob_store = blob_store
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def process(
        self,
        session_id: str,
        agent_id: str,
        file_path: Optional[str] = None,
        raw_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        extract_tables: bool = True,
        extract_images: bool = True,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a document into the session's graph.

        Provide either ``file_path`` (path on disk) or ``raw_bytes`` + ``filename``
        (e.g. from an upload).

        Returns summary dict with all created node/edge IDs.
        """
        session = self._session_mgr.get_session(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        graph = self._get_graph(session)
        if not graph:
            raise RuntimeError("Graph not available for this session")

        csize = chunk_size or self._chunk_size
        coverlap = chunk_overlap or self._chunk_overlap
        meta = metadata or {}

        # --- resolve file ---
        if raw_bytes is not None:
            fname = filename or "upload"
            file_hash = hashlib.sha256(raw_bytes).hexdigest()
        elif file_path:
            p = Path(file_path)
            if not p.exists():
                raise FileNotFoundError(f"File not found: {file_path}")
            fname = p.name
            raw_bytes = p.read_bytes()
            file_hash = hashlib.sha256(raw_bytes).hexdigest()
        else:
            raise ValueError("Provide file_path or raw_bytes")

        document_id = file_hash[:16]

        # --- try the full extraction engine first ---
        extracted = self._try_extraction_engine(
            file_path, raw_bytes, fname, session.graph_namespace,
            document_id, extract_tables, extract_images,
        )

        # --- if extraction engine isn't available or returned empty, do basic ---
        if extracted is None or not extracted.get("text", "").strip():
            extracted = self._basic_extract(raw_bytes, fname)

        # --- chunk the text ---
        full_text = extracted.get("text", "")
        chunks = self._chunk_text(full_text, csize, coverlap)
        tables = extracted.get("tables", [])
        images = extracted.get("images", [])
        doc_metadata = extracted.get("metadata", {})

        # --- build graph ---
        from ..core.graph_structures import GraphNode, GraphEdge

        ctx_props = {
            "_ctx_session_id": session.session_id,
            "_ctx_agent_id": agent_id,
            "_ctx_source": "document_processor",
        }

        result = {
            "document_id": document_id,
            "source_node": None,
            "document_node": None,
            "chunk_nodes": [],
            "table_nodes": [],
            "image_nodes": [],
            "edges": [],
            "chunk_count": 0,
            "table_count": 0,
            "image_count": 0,
        }

        # 1) Source node
        source_id = str(uuid.uuid4())
        source_node = GraphNode(
            id=source_id,
            label="Source",
            properties={
                "source_type": Path(fname).suffix.lstrip(".") or "document",
                "filename": fname,
                "file_hash": file_hash,
                "size_bytes": len(raw_bytes),
                **ctx_props,
                **meta,
            },
        )
        graph.add_node(source_node)
        result["source_node"] = source_id

        # Link Source to the root node (Context or Session) if it exists
        # The root node has the session_id or context_id as its ID
        root_node = graph.get_node(session_id)
        if root_node:
            graph.add_edge(GraphEdge(
                id=str(uuid.uuid4()),
                source=session_id, target=source_id,
                label="HAS_DOCUMENT",
                properties={"filename": fname},
            ))
            result["edges"].append(f"root→{source_id[:8]}")

        # 2) Document node
        doc_id = str(uuid.uuid4())
        doc_node = GraphNode(
            id=doc_id,
            label="Document",
            properties={
                "title": doc_metadata.get("title") or fname,
                "text": full_text[:50000],  # cap to prevent huge props
                "page_count": doc_metadata.get("page_count", 0),
                "char_count": len(full_text),
                "document_id": document_id,
                **ctx_props,
            },
        )
        graph.add_node(doc_node)
        result["document_node"] = doc_id

        # Edge: Source → Document
        e1 = GraphEdge(
            id=str(uuid.uuid4()),
            source=source_id, target=doc_id,
            label="SOURCE_OF", properties={},
        )
        graph.add_edge(e1)
        result["edges"].append(e1.id)

        # 3) Chunk nodes
        for i, chunk_text in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            chunk_node = GraphNode(
                id=chunk_id,
                label="Chunk",
                properties={
                    "content": chunk_text,
                    "order": i,
                    "char_count": len(chunk_text),
                    "parent_id": doc_id,
                    **ctx_props,
                },
            )
            graph.add_node(chunk_node)
            result["chunk_nodes"].append(chunk_id)

            # Edge: Document → Chunk
            edge = GraphEdge(
                id=str(uuid.uuid4()),
                source=doc_id, target=chunk_id,
                label="PARENT_OF", properties={"order": i},
            )
            graph.add_edge(edge)
            result["edges"].append(edge.id)

        result["chunk_count"] = len(chunks)

        # 4) Table nodes + Indicator extraction
        indicator_count = 0
        for i, table in enumerate(tables):
            table_id = str(uuid.uuid4())
            table_title = table.get("title", f"Table_{i+1}")
            headers = table.get("headers", [])
            rows = table.get("rows", [])

            tnode = GraphNode(
                id=table_id,
                label="Table",
                properties={
                    "title": table_title,
                    "headers": headers,
                    "rows": rows,
                    "row_count": len(rows),
                    "column_count": len(headers),
                    "page": table.get("page_number", 0),
                    **ctx_props,
                },
            )
            graph.add_node(tnode)
            result["table_nodes"].append(table_id)

            edge = GraphEdge(
                id=str(uuid.uuid4()),
                source=doc_id, target=table_id,
                label="PARENT_OF", properties={"type": "table"},
            )
            graph.add_edge(edge)
            result["edges"].append(edge.id)

            # Extract Indicator nodes from numeric cells
            try:
                from ..intelligence.table_enricher import enrich_table
                indicators, _ = enrich_table(
                    headers=headers,
                    rows=[[str(c) for c in row] for row in rows],
                    source_entity="",
                    table_title=table_title,
                )
                for ind in indicators:
                    ind_dict = ind.to_node_dict()
                    ind_node = GraphNode(
                        id=ind_dict["id"],
                        label="Indicator",
                        properties={**ind_dict["properties"], **ctx_props},
                    )
                    graph.add_node(ind_node)
                    # Indicator --EXTRACTED_FROM--> Table
                    graph.add_edge(GraphEdge(
                        id=str(uuid.uuid4()),
                        source=ind_dict["id"], target=table_id,
                        label="EXTRACTED_FROM", properties={},
                    ))
                    result["edges"].append(ind_dict["id"])
                    indicator_count += 1
            except Exception as exc:
                logger.warning("Table enrichment failed for table %d: %s", i, exc)

        result["table_count"] = len(tables)
        result["indicator_count"] = indicator_count

        # 5) Image nodes (with OCR for scanned documents)
        for i, img in enumerate(images):
            img_id = str(uuid.uuid4())

            # OCR: extract text from image if available
            ocr_text = ""
            img_path = img.get("path", "")
            if img_path:
                try:
                    from ..intelligence.ocr import extract_text_from_image, is_available
                    if is_available():
                        ocr_text = extract_text_from_image(img_path)
                except Exception:
                    pass

            inode = GraphNode(
                id=img_id,
                label="Image",
                properties={
                    "description": img.get("description", ""),
                    "format": img.get("format", ""),
                    "page": img.get("page_number", 0),
                    "width": img.get("width", 0),
                    "height": img.get("height", 0),
                    "ocr_text": ocr_text,
                    **ctx_props,
                },
            )
            graph.add_node(inode)
            result["image_nodes"].append(img_id)

            # If OCR extracted text, also add it as a chunk for search/extraction
            if ocr_text and len(ocr_text) > 20:
                ocr_chunk_id = str(uuid.uuid4())
                graph.add_node(GraphNode(
                    id=ocr_chunk_id,
                    label="Chunk",
                    properties={
                        "content": ocr_text,
                        "source_type": "ocr",
                        "page": img.get("page_number", 0),
                        **ctx_props,
                    },
                ))
                graph.add_edge(GraphEdge(
                    id=str(uuid.uuid4()),
                    source=img_id, target=ocr_chunk_id,
                    label="OCR_TEXT", properties={},
                ))
                result["edges"].append(ocr_chunk_id)

            edge = GraphEdge(
                id=str(uuid.uuid4()),
                source=doc_id, target=img_id,
                label="PARENT_OF", properties={"type": "image"},
            )
            graph.add_edge(edge)
            result["edges"].append(edge.id)

        result["image_count"] = len(images)

        # --- store raw file as blob (if blob store available) ---
        if self._blob_store:
            import mimetypes
            mime = mimetypes.guess_type(fname)[0] or "application/octet-stream"
            self._blob_store.store(
                session_id=session.session_id,
                data=raw_bytes,
                filename=fname,
                mime_type=mime,
                agent_id=agent_id,
            )

        # --- provenance ---
        all_node_ids = (
            [source_id, doc_id]
            + result["chunk_nodes"]
            + result["table_nodes"]
            + result["image_nodes"]
        )
        for nid in all_node_ids:
            self._agent_registry.record_provenance(ProvenanceRecord(
                session_id=session.session_id,
                agent_id=agent_id,
                operation="ingest",
                target_type="node",
                target_id=nid,
                metadata={"pipeline": "document_processor", "document_id": document_id},
            ))

        # --- save graph ---
        if self._graph_registry:
            self._graph_registry.save_graph(session.graph_namespace, create_checkpoint=False)

        self._session_mgr.touch_session(session_id)

        logger.info(
            f"Processed '{fname}' into session {session_id}: "
            f"{result['chunk_count']} chunks, {result['table_count']} tables, "
            f"{result['image_count']} images"
        )
        return result

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _try_extraction_engine(
        self, file_path, raw_bytes, filename, namespace,
        document_id, extract_tables, extract_images,
    ) -> Optional[Dict[str, Any]]:
        """Try the full extraction engine. Returns None if unavailable."""
        try:
            from ..extraction.config import ExtractionConfig
            from ..extraction.extract_engine import ExtractionEngine

            # Write bytes to temp file if we only have raw_bytes
            if file_path and Path(file_path).exists():
                actual_path = file_path
                tmp_path = None
            else:
                tmp_dir = Path("contextcore_data/tmp")
                tmp_dir.mkdir(parents=True, exist_ok=True)
                tmp_path = tmp_dir / filename
                tmp_path.write_bytes(raw_bytes)
                actual_path = str(tmp_path)

            detect = ["TEXT"]
            if extract_tables:
                detect.append("TABLES")
            if extract_images:
                detect.append("IMAGES")

            config = ExtractionConfig(
                file_path=actual_path,
                reader="AUTO",
                detect=detect,
                namespace=namespace,
                document_id=document_id,
            )
            engine = ExtractionEngine(config)
            engine.run()

            # Read the normalized output
            norm_path = Path(
                f"contextcore_data/namespaces/{namespace}/documents/{document_id}"
                f"/normalized/normalized_v1.json"
            )
            if norm_path.exists():
                with open(norm_path, encoding="utf-8") as f:
                    normalized = json.load(f)

                full_text = ""
                tables = []
                images = []

                pages = normalized.get("content", {}).get("pages", [])
                for page in pages:
                    full_text += page.get("text", "") + "\n\n"

                for t in normalized.get("content", {}).get("tables", []):
                    tables.append({
                        "title": t.get("title", ""),
                        "headers": t.get("headers", []),
                        "rows": t.get("rows", []),
                        "page_number": t.get("page_number", 0),
                    })

                for img in normalized.get("content", {}).get("images", []):
                    images.append({
                        "description": img.get("description", ""),
                        "format": img.get("format", ""),
                        "page_number": img.get("page_number", 0),
                        "width": img.get("width", 0),
                        "height": img.get("height", 0),
                    })

                # Cleanup temp
                if tmp_path and tmp_path.exists():
                    tmp_path.unlink()

                return {
                    "text": full_text.strip(),
                    "tables": tables,
                    "images": images,
                    "metadata": normalized.get("metadata", {}),
                }

            if tmp_path and tmp_path.exists():
                tmp_path.unlink()

        except (ImportError, FileNotFoundError, Exception) as e:
            logger.debug(f"Extraction engine not available, falling back to basic: {e}")

        return None

    def _basic_extract(self, raw_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Fallback: extract text from common formats without the full engine."""
        ext = Path(filename).suffix.lower()
        text = ""
        tables = []
        images = []
        metadata = {}

        if ext == ".pdf":
            text, tables, images, metadata = self._extract_pdf_basic(raw_bytes)
        elif ext in (".txt", ".md", ".rst", ".log"):
            text = raw_bytes.decode("utf-8", errors="replace")
        elif ext == ".csv":
            text = raw_bytes.decode("utf-8", errors="replace")
            tables = self._parse_csv(text)
        elif ext in (".json", ".jsonl"):
            text = raw_bytes.decode("utf-8", errors="replace")
        elif ext in (".html", ".htm"):
            text = self._strip_html(raw_bytes.decode("utf-8", errors="replace"))
        else:
            # Try to decode as text
            try:
                text = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = f"[Binary file: {filename}, {len(raw_bytes)} bytes]"

        return {"text": text, "tables": tables, "images": images, "metadata": metadata}

    def _extract_pdf_basic(self, raw_bytes: bytes):
        """Try pdfplumber or PyPDF2 for basic PDF text."""
        text = ""
        tables = []
        images = []
        metadata = {}

        # Try pdfplumber first (better table support)
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                metadata["page_count"] = len(pdf.pages)
                metadata["title"] = pdf.metadata.get("Title", "") if pdf.metadata else ""
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n\n"
                    # Extract tables
                    for tbl in (page.extract_tables() or []):
                        if tbl and len(tbl) > 1:
                            tables.append({
                                "headers": [str(h) for h in (tbl[0] or [])],
                                "rows": [[str(c) for c in (row or [])] for row in tbl[1:]],
                                "page_number": page.page_number,
                            })
            return text.strip(), tables, images, metadata
        except ImportError:
            pass

        # Fall back to PyPDF2
        try:
            from PyPDF2 import PdfReader
            import io
            reader = PdfReader(io.BytesIO(raw_bytes))
            metadata["page_count"] = len(reader.pages)
            info = reader.metadata
            if info:
                metadata["title"] = info.get("/Title", "") or ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n\n"
            return text.strip(), tables, images, metadata
        except ImportError:
            pass

        return "[PDF extraction requires pdfplumber or PyPDF2]", tables, images, metadata

    def _parse_csv(self, text: str) -> List[Dict]:
        """Parse CSV text into a single table."""
        lines = text.strip().split("\n")
        if len(lines) < 2:
            return []
        headers = [h.strip().strip('"') for h in lines[0].split(",")]
        rows = []
        for line in lines[1:]:
            row = [c.strip().strip('"') for c in line.split(",")]
            rows.append(row)
        return [{"headers": headers, "rows": rows, "title": "CSV Data"}]

    def _strip_html(self, html: str) -> str:
        """Simple HTML tag stripping."""
        return re.sub(r"<[^>]+>", " ", html)

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def _chunk_text(
        self, text: str, chunk_size: int, overlap: int
    ) -> List[str]:
        """
        Split text into overlapping chunks, preferring paragraph/sentence
        boundaries.
        """
        if not text or len(text) <= chunk_size:
            return [text] if text else []

        chunks = []
        # Split by paragraphs first
        paragraphs = re.split(r"\n{2,}", text)
        current = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            if len(current) + len(para) + 2 <= chunk_size:
                current = (current + "\n\n" + para).strip()
            else:
                if current:
                    chunks.append(current)
                # If single paragraph exceeds chunk_size, split by sentences
                if len(para) > chunk_size:
                    sentence_chunks = self._chunk_by_sentences(para, chunk_size, overlap)
                    chunks.extend(sentence_chunks)
                    current = ""
                else:
                    # Start new chunk with overlap from previous
                    if chunks and overlap > 0:
                        prev = chunks[-1]
                        tail = prev[-overlap:] if len(prev) > overlap else prev
                        current = tail + "\n\n" + para
                    else:
                        current = para

        if current:
            chunks.append(current)

        return chunks

    def _chunk_by_sentences(
        self, text: str, chunk_size: int, overlap: int
    ) -> List[str]:
        """Split long text by sentence boundaries."""
        sentences = re.split(r"(?<=[.!?])\s+", text)
        chunks = []
        current = ""

        for sent in sentences:
            if len(current) + len(sent) + 1 <= chunk_size:
                current = (current + " " + sent).strip()
            else:
                if current:
                    chunks.append(current)
                current = sent

        if current:
            chunks.append(current)

        return chunks

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_graph(self, session):
        if self._graph_registry is None:
            return None
        return self._graph_registry.get_graph(
            session.graph_namespace, load_if_missing=True
        )
