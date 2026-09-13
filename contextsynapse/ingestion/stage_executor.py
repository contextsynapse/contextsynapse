"""
Stage Executor
==============
Unified execution engine for scenario-driven pipelines.

Replaces the three disconnected pipeline systems with a single composable
stage executor that supports both run-all and step-by-step execution.

Each stage reads from PipelineContext and writes results back to it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .pipeline_context import ClassificationResult, PipelineContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers (reused from pipeline_runner.py)
# ---------------------------------------------------------------------------

from .chunking import chunk_text as _chunk_text  # canonical implementation


def _simhash(text: str, hash_bits: int = 64) -> int:
    """Compute a SimHash fingerprint for near-duplicate detection.

    Uses word-level 3-grams hashed to bit vectors. Two texts with a small
    Hamming distance between their SimHashes are likely near-duplicates.
    """
    normalised = re.sub(r"\s+", " ", text.lower().strip())
    tokens = normalised.split()
    if len(tokens) < 3:
        tokens = list(normalised)  # character-level fallback for short text

    v = [0] * hash_bits
    for i in range(max(1, len(tokens) - 2)):
        gram = " ".join(tokens[i:i + 3])
        h = int(hashlib.md5(gram.encode()).hexdigest(), 16)
        for j in range(hash_bits):
            if h & (1 << j):
                v[j] += 1
            else:
                v[j] -= 1

    fingerprint = 0
    for j in range(hash_bits):
        if v[j] > 0:
            fingerprint |= (1 << j)
    return fingerprint


def _hamming_distance(a: int, b: int) -> int:
    """Number of differing bits between two integers."""
    return bin(a ^ b).count("1")


def _content_hash(text: str) -> str:
    """SHA-256 of raw text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalised_hash(text: str) -> str:
    """SHA-256 of normalised text (lowercase, collapsed whitespace)."""
    normalised = re.sub(r"\s+", " ", text.lower().strip())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _entity_id(entity_type: str, entity_name: str) -> str:
    """Deterministic entity ID from type + canonical name."""
    canonical = f"{entity_type}:{entity_name.lower().strip()}"
    h = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    return f"entity_{h}"


def _parse_entities_fallback(text: str) -> List[Dict[str, Any]]:
    """Regex fallback to extract entities from non-JSON LLM responses."""
    entities = []
    patterns = [
        r'(\w+):\s*([^(]+)\s*\(([^)]+)\)',
        r'Name:\s*([^,]+),\s*Type:\s*([^,]+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if len(match) >= 2:
                entities.append({
                    "name": match[1].strip() if len(match) > 1 else match[0].strip(),
                    "type": match[2].strip() if len(match) > 2 else "Entity",
                })
    return entities


# ---------------------------------------------------------------------------
# Stage Executor
# ---------------------------------------------------------------------------

class StageExecutor:
    """
    Executes pipeline stages against a shared PipelineContext.

    Supports:
      - execute_stage(name, ctx) — run one stage
      - execute_next(ctx) — run the next pending stage, then pause
      - execute_all(ctx) — run all remaining stages without pausing
    """

    def __init__(self, graph_registry=None, on_sub_step=None, aiql_executor=None):
        self.graph_registry = graph_registry
        self.on_sub_step = on_sub_step  # Callable(run_id, stage, message, progress)
        self._llm = None
        self._embeddings = None
        self._aiql_executor = aiql_executor

        # Dispatch table: stage_name -> method
        self._stage_handlers = {
            "PARSE_FILE": self._stage_parse_file,
            "CLASSIFY": self._stage_classify,
            "CHUNK": self._stage_chunk,
            "DEDUP": self._stage_dedup,
            "MAP_TABULAR": self._stage_map_tabular,
            "EXTRACT": self._stage_extract,
            "EXTRACT_FACTS": self._stage_extract_facts,
            "EMBED": self._stage_embed,
            "INDEX_BM25": self._stage_index_bm25,
            "STORE_VECTORS": self._stage_store_vectors,
            "CANONICALIZE": self._stage_canonicalize,
            "ENHANCE_GRAPH": self._stage_enhance_graph,
            "PERSIST": self._stage_persist,
            "IMPORT_GRAPH": self._stage_import_graph,
            "VALIDATE_SCHEMA": self._stage_validate_schema,
            "SDLC_SCAN": self._stage_sdlc_scan,
            # SDLC sub-stages (resumable)
            "SDLC_SCAN_FILES": self._stage_sdlc_scan_files,
            "SDLC_SCAN_GITHUB": self._stage_sdlc_scan_github,
            "SDLC_SCAN_GIT": self._stage_sdlc_scan_git,
            "SDLC_SCAN_EDGES": self._stage_sdlc_scan_edges,
            "SDLC_SCAN_CU": self._stage_sdlc_scan_cu,
        }

    # ------------------------------------------------------------------
    # LLM / Embedding accessors (lazy)
    # ------------------------------------------------------------------

    def _get_aiql_executor(self):
        """Get or lazily create an AIQLExecutor."""
        if self._aiql_executor is None and self.graph_registry:
            try:
                from ..aiql.engine.executor import AIQLExecutor
                self._aiql_executor = AIQLExecutor(graph_registry=self.graph_registry)
            except Exception as e:
                logger.warning("Could not init AIQLExecutor: %s", e)
        return self._aiql_executor

    def _get_llm(self, model: Optional[str] = None):
        if self._llm is None and model:
            try:
                from ..llm import get_llm_client
                # Support "provider:model" format (e.g. "groq:gpt-oss-120b")
                provider, _, model_name = (model or "").partition(":")
                if model_name:
                    self._llm = get_llm_client(provider=provider, model=model_name)
                else:
                    self._llm = get_llm_client()
            except ImportError:
                pass
            except Exception as e:
                logger.debug("LLM not available: %s", e)
        return self._llm

    def _get_embeddings(self, model: Optional[str] = None):
        if self._embeddings is None and model:
            try:
                from ..models.embedding_service import EmbeddingService
                self._embeddings = EmbeddingService()
            except ImportError:
                pass
            except Exception as e:
                logger.debug("Embeddings not available: %s", e)
        return self._embeddings

    # ------------------------------------------------------------------
    # Sub-step emission
    # ------------------------------------------------------------------

    def _emit_sub_step(self, ctx: PipelineContext, message: str, progress: float = None):
        """Record a sub-step on the context and notify listeners."""
        ctx.record_sub_step(message, progress)
        if self.on_sub_step:
            try:
                self.on_sub_step(ctx.run_id, ctx.current_stage or "unknown", message, progress)
            except Exception:
                pass  # never let sub-step emission break execution

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute_stage(self, stage_name: str, ctx: PipelineContext) -> PipelineContext:
        """Execute a single named stage."""
        handler = self._stage_handlers.get(stage_name)
        if not handler:
            ctx.record_stage(stage_name, "failed", error=f"Unknown stage: {stage_name}")
            return ctx

        ctx.status = "running"
        started = datetime.now(timezone.utc).isoformat()

        try:
            summary = handler(ctx)
            ctx.record_stage(stage_name, "completed", summary=summary or {})
        except Exception as e:
            logger.error("Stage %s failed: %s", stage_name, e, exc_info=True)
            ctx.record_stage(stage_name, "failed", error=str(e))
            ctx.status = "failed"

        return ctx

    def execute_next(self, ctx: PipelineContext) -> PipelineContext:
        """Execute the next pending stage, then pause."""
        if ctx.is_done:
            ctx.status = "completed"
            return ctx

        stage_name = ctx.current_stage
        if not stage_name:
            ctx.status = "completed"
            return ctx

        self.execute_stage(stage_name, ctx)

        # Advance pointer
        ctx.advance()

        if ctx.status != "failed":
            if ctx.is_done:
                ctx.status = "completed"
            else:
                ctx.status = "paused"

        return ctx

    def execute_all(
        self,
        ctx: PipelineContext,
        on_stage: Optional[Callable] = None,
    ) -> PipelineContext:
        """Execute all remaining stages without pausing."""
        while not ctx.is_done and ctx.status != "failed":
            stage_name = ctx.current_stage
            if on_stage:
                on_stage(stage_name, "running", ctx)

            self.execute_stage(stage_name, ctx)
            ctx.advance()

            if on_stage and ctx.stage_results:
                last = ctx.stage_results[-1]
                on_stage(stage_name, last.get("status", "completed"), ctx)

            if ctx.status == "failed":
                break

        if ctx.status != "failed":
            ctx.status = "completed"

        return ctx

    # ------------------------------------------------------------------
    # PARSE_FILE — extract content from uploaded file
    # ------------------------------------------------------------------

    def _stage_parse_file(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Use ExtractorRegistry to parse the uploaded file."""
        from ..extraction.registry import ExtractorRegistry, ExtractionResult

        file_path = ctx.source_bytes_path
        filename = ctx.source_filename or ""

        # If we have text input instead of a file, create a minimal extraction result
        if not file_path and ctx.source_text:
            ctx.extraction_result = {
                "pages": [{"page_no": 1, "text": ctx.source_text, "content": ctx.source_text}],
                "tables": [],
                "metadata": {"title": filename or "Text Input", "file_type": "txt"},
                "errors": [],
            }
            return {"source": "text", "pages": 1, "tables": 0}

        if not file_path or not os.path.exists(file_path):
            raise FileNotFoundError(f"Source file not found: {file_path}")

        self._emit_sub_step(ctx, f"Parsing file: {filename or file_path}")

        # Try extension-based extractor lookup
        ext = Path(filename).suffix.lower() if filename else Path(file_path).suffix.lower()

        # Direct extractor mapping
        extractor = None
        extractor_map = {
            ".xlsx": "excel", ".xls": "excel",
            ".csv": "csv", ".tsv": "csv",
            ".pdf": "pdf", ".docx": "docx",
            ".txt": "txt", ".md": "txt",
            ".html": "html", ".htm": "html",
            ".zip": "chat_export",
        }

        extractor_type = extractor_map.get(ext)

        # Auto-detect .json files — could be chat export or regular data
        if ext == ".json" and not extractor_type:
            try:
                from ..extraction.extractors.chat_export_extractor import ChatExportExtractor
                _test_ext = ChatExportExtractor(type("C", (), {"file_path": file_path})())
                if _test_ext.supports_format(file_path):
                    extractor_type = "chat_export"
            except Exception:
                pass
            if not extractor_type:
                extractor_type = "txt"  # fallback: treat JSON as text

        if extractor_type:
            extractor = self._create_extractor(extractor_type, file_path)

        if not extractor:
            # Fallback: try auto-detect from registry
            try:
                from ..extraction.registry import ExtractionConfig
                config = ExtractionConfig()
            except ImportError:
                config = type("Config", (), {})()
            extractor = ExtractorRegistry.auto_detect(file_path, config)

        if not extractor:
            raise ValueError(f"No extractor available for file type: {ext}")

        self._emit_sub_step(ctx, f"Using {extractor_type or 'auto'} extractor for {ext}")

        # Pass sub-step callback to extractor if supported
        if hasattr(extractor, 'progress_callback'):
            extractor.progress_callback = lambda msg, prog=None: self._emit_sub_step(ctx, msg, prog)

        result = extractor.extract(file_path)

        self._emit_sub_step(ctx, f"Extracted {len(result.pages)} pages, {len(result.tables)} tables")

        # Serialize ExtractionResult to dict for PipelineContext
        ctx.extraction_result = {
            "pages": result.pages,
            "tables": result.tables,
            "metadata": result.metadata,
            "images": result.images if hasattr(result, "images") else [],
            "errors": result.errors,
        }
        ctx.tables = result.tables

        return {
            "file_type": ext,
            "pages": len(result.pages),
            "tables": len(result.tables),
            "errors": len(result.errors),
        }

    def _create_extractor(self, extractor_type: str, file_path: str):
        """Create an extractor instance by type name."""
        try:
            # Create a minimal config object
            try:
                from ..extraction.config import ExtractionConfig
                config = ExtractionConfig(file_path=file_path)
            except Exception:
                config = type("Config", (), {
                    "file_path": file_path,
                    "pages_mode": type("Mode", (), {"value": "full"})(),
                    "pages_range": None,
                    "pages_list": None,
                    "detect": ["TEXT"],
                    "reader": "AUTO",
                    "parse_metadata": True,
                    "store_intermediate": False,
                    "output_dir": "contextcore_data",
                    "namespace": None,
                    "document_id": None,
                    "use_layout_parser": False,
                    "layout_detection_method": "auto",
                    "post_process": True,
                    "post_processing_config": None,
                })()

            if extractor_type == "excel":
                from ..extraction.extractors.excel_extractor import ExcelExtractor
                return ExcelExtractor(config)
            elif extractor_type == "csv":
                from ..extraction.extractors.csv_extractor import CsvExtractor
                return CsvExtractor(config)
            elif extractor_type == "pdf":
                from ..extraction.extractors.pdf_extractor import PdfExtractor
                return PdfExtractor(config)
            elif extractor_type == "docx":
                from ..extraction.extractors.docx_extractor import DocxExtractor
                return DocxExtractor(config)
            elif extractor_type == "txt":
                from ..extraction.extractors.txt_extractor import TxtExtractor
                return TxtExtractor(config)
            elif extractor_type == "html":
                from ..extraction.extractors.html_extractor import HtmlExtractor
                return HtmlExtractor(config)
            elif extractor_type == "chat_export":
                from ..extraction.extractors.chat_export_extractor import ChatExportExtractor
                return ChatExportExtractor(config)
        except Exception as e:
            logger.warning("Failed to create %s extractor: %s", extractor_type, e)
        return None

    # ------------------------------------------------------------------
    # CLASSIFY — detect file type and content structure
    # ------------------------------------------------------------------

    def _stage_classify(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Run InputClassifier on extraction result."""
        from .input_classifier import InputClassifier

        if not ctx.extraction_result:
            raise ValueError("No extraction result to classify — run PARSE_FILE first")

        classifier = InputClassifier()
        classification = classifier.classify(
            ctx.extraction_result,
            ctx.source_filename or "",
        )

        ctx.classification = classification.to_dict()

        return {
            "file_type": classification.file_type,
            "structure_type": classification.structure_type,
            "confidence": classification.confidence,
        }

    # ------------------------------------------------------------------
    # CHUNK — split text content into chunks
    # ------------------------------------------------------------------

    def _stage_chunk(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Chunk text content from extraction result."""
        if not ctx.extraction_result:
            # Fallback: if source_text is set, create extraction_result on the fly
            if ctx.source_text:
                ctx.extraction_result = {
                    "pages": [{"page_no": 1, "text": ctx.source_text}],
                    "tables": [],
                    "metadata": {"title": ctx.source_filename or "Text Input", "file_type": "txt"},
                }
            else:
                raise ValueError("No extraction result — run PARSE_FILE first")

        pages = ctx.extraction_result.get("pages", [])
        all_text = "\n\n".join(p.get("text", "") for p in pages).strip()

        if not all_text:
            ctx.chunks = []
            return {"chunks": 0, "source": "no_text"}

        max_chars = ctx.pipeline_params.get("chunk_size", 2000)
        raw_chunks = _chunk_text(all_text, max_chars)

        ctx.chunks = [
            {
                "id": str(uuid.uuid4()),
                "index": i,
                "text": chunk,
                "char_count": len(chunk),
            }
            for i, chunk in enumerate(raw_chunks)
        ]

        return {"chunks": len(ctx.chunks), "total_chars": len(all_text)}

    # ------------------------------------------------------------------
    # DEDUP — content and semantic hash deduplication
    # ------------------------------------------------------------------

    def _stage_dedup(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Deduplicate chunks using content hashing and SimHash fingerprinting.

        Two layers:
        1. **Exact hash** — SHA-256 of raw and normalised text. Catches exact
           duplicates and trivial reformattings (whitespace, case changes).
        2. **SimHash** — 64-bit fingerprint based on word 3-grams. Catches
           near-duplicates (paraphrases, minor edits). Configurable Hamming
           distance threshold (default: 3 bits out of 64).

        Checks both within the current batch (cross-chunk) and against
        existing nodes already in the target graph.

        Pipeline params:
            dedup_enabled: bool (default True)
            dedup_hamming_threshold: int (default 6 — bits that may differ out of 64)
            dedup_skip_short: int (default 100 — skip chunks < N chars)
        """
        if not ctx.chunks:
            return {"chunks": 0, "skipped": 0, "reason": "no_chunks"}

        enabled = ctx.pipeline_params.get("dedup_enabled", True)
        if not enabled:
            return {"chunks": len(ctx.chunks), "skipped": 0, "reason": "disabled"}

        hamming_threshold = ctx.pipeline_params.get("dedup_hamming_threshold", 6)
        skip_short = ctx.pipeline_params.get("dedup_skip_short", 100)

        # Collect existing hashes from the target graph
        existing_hashes = set()      # SHA-256 content hashes
        existing_norm_hashes = set()  # SHA-256 normalised hashes
        existing_simhashes = []       # (node_id, simhash_int) pairs

        graph = None
        ns = getattr(ctx, "graph_namespace", "") or getattr(ctx, "namespace", "")
        if self.graph_registry and ns:
            try:
                graph = self.graph_registry.get_graph(ns, load_if_missing=True)
                if graph is None and ":" in ns:
                    graph = self.graph_registry.get_graph(ns.split(":", 1)[1], load_if_missing=True)
            except Exception:
                pass

        if graph:
            for node in graph.get_all_nodes():
                props = node.properties if hasattr(node, "properties") else {}
                ch = props.get("content_hash")
                if ch:
                    existing_hashes.add(ch)
                nh = props.get("normalised_hash")
                if nh:
                    existing_norm_hashes.add(nh)
                sh = props.get("simhash")
                if sh is not None:
                    try:
                        existing_simhashes.append((node.id, int(sh)))
                    except (ValueError, TypeError):
                        pass

        # Dedup within current batch + against existing graph
        batch_hashes = set()
        batch_norm_hashes = set()
        batch_simhashes = []  # (chunk_id, simhash_int)

        kept = []
        skipped_exact = 0
        skipped_near = 0
        skipped_short = 0

        for chunk in ctx.chunks:
            text = chunk.get("text", "")

            # Skip very short chunks (headers, footers, etc.)
            if len(text) < skip_short:
                chunk["content_hash"] = _content_hash(text)
                chunk["normalised_hash"] = _normalised_hash(text)
                chunk["simhash"] = str(_simhash(text))
                kept.append(chunk)
                skipped_short += 1  # counted but still kept
                continue

            ch = _content_hash(text)
            nh = _normalised_hash(text)
            sh = _simhash(text)

            # Layer 1: exact hash check
            if ch in existing_hashes or ch in batch_hashes:
                skipped_exact += 1
                logger.info("[DEDUP] Exact duplicate skipped (chunk %s, %d chars)",
                            chunk.get("index", "?"), len(text))
                continue

            if nh in existing_norm_hashes or nh in batch_norm_hashes:
                skipped_exact += 1
                logger.info("[DEDUP] Normalised duplicate skipped (chunk %s, %d chars)",
                            chunk.get("index", "?"), len(text))
                continue

            # Layer 2: SimHash near-duplicate check
            is_near_dup = False
            for _, existing_sh in existing_simhashes:
                if _hamming_distance(sh, existing_sh) <= hamming_threshold:
                    is_near_dup = True
                    break
            if not is_near_dup:
                for _, batch_sh in batch_simhashes:
                    if _hamming_distance(sh, batch_sh) <= hamming_threshold:
                        is_near_dup = True
                        break

            if is_near_dup:
                skipped_near += 1
                logger.info("[DEDUP] Near-duplicate skipped (chunk %s, hamming ≤ %d)",
                            chunk.get("index", "?"), hamming_threshold)
                continue

            # Not a duplicate — stamp hashes and keep
            chunk["content_hash"] = ch
            chunk["normalised_hash"] = nh
            chunk["simhash"] = str(sh)
            batch_hashes.add(ch)
            batch_norm_hashes.add(nh)
            batch_simhashes.append((chunk["id"], sh))
            kept.append(chunk)

        # Re-index kept chunks
        for i, chunk in enumerate(kept):
            chunk["index"] = i

        ctx.chunks = kept

        total_skipped = skipped_exact + skipped_near
        if total_skipped:
            logger.info("[DEDUP] Kept %d/%d chunks (exact=%d, near=%d skipped)",
                        len(kept), len(kept) + total_skipped, skipped_exact, skipped_near)

        return {
            "chunks_before": len(kept) + total_skipped,
            "chunks_after": len(kept),
            "skipped_exact": skipped_exact,
            "skipped_near_duplicate": skipped_near,
            "short_chunks": skipped_short,
            "hamming_threshold": hamming_threshold,
        }

    # ------------------------------------------------------------------
    # MAP_TABULAR — convert structured tables to nodes/edges
    # ------------------------------------------------------------------

    def _stage_map_tabular(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Map tabular data to graph nodes and edges."""
        from .stages.tabular_to_graph import TabularToGraphMapper

        tables = ctx.tables or (ctx.extraction_result or {}).get("tables", [])
        if not tables:
            return {"nodes": 0, "edges": 0, "reason": "no_tables"}

        # Need classification for column role mapping
        classification = ClassificationResult.from_dict(ctx.classification) if ctx.classification else None
        if not classification:
            # Quick classify if not done yet
            from .input_classifier import InputClassifier
            classifier = InputClassifier()
            classification = classifier.classify(
                ctx.extraction_result or {"tables": tables},
                ctx.source_filename or "",
            )
            ctx.classification = classification.to_dict()

        mapper = TabularToGraphMapper()
        nodes, edges = mapper.map(tables, classification)

        # Merge with any existing nodes/edges (from other stages)
        ctx.nodes.extend(nodes)
        ctx.edges.extend(edges)

        return {"nodes": len(nodes), "edges": len(edges)}

    # ------------------------------------------------------------------
    # EXTRACT — LLM entity & relationship extraction from chunks
    # ------------------------------------------------------------------

    def _stage_extract(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Extract entities and relationships from text chunks using LLM."""
        llm_model = ctx.pipeline_params.get("llm_model")
        llm = self._get_llm(llm_model)

        if not llm or not llm_model:
            return {"entities": 0, "relationships": 0, "reason": "no_llm_configured"}

        chunks = ctx.chunks
        if not chunks:
            return {"entities": 0, "relationships": 0, "reason": "no_chunks"}

        entity_map: Dict[str, Dict] = {}
        all_relationships = []
        all_facts = []
        chunk_entity_links = []

        schema = ctx.pipeline_params.get("schema")

        # Parallel chunk extraction — up to 3 concurrent LLM calls per document
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os
        _extract_workers = int(os.environ.get("CONTEXTSYNAPSE_EXTRACT_WORKERS") or os.environ.get("AICONTEXTDB_EXTRACT_WORKERS", "3"))

        def _extract_one(chunk):
            text = chunk.get("text", "")
            if not text.strip():
                return chunk, {"entities": [], "relationships": []}
            return chunk, self._extract_from_chunk(llm, llm_model, text, schema)

        valid_chunks = [c for c in chunks if c.get("text", "").strip()]
        chunk_results = []
        with ThreadPoolExecutor(max_workers=min(_extract_workers, len(valid_chunks) or 1)) as pool:
            futures = {pool.submit(_extract_one, c): c for c in valid_chunks}
            for future in as_completed(futures):
                try:
                    chunk_results.append(future.result())
                except Exception as e:
                    logger.warning("[EXTRACT] Chunk extraction failed: %s", e)

        for chunk, result in chunk_results:
            for ent in result.get("entities", []):
                # Accept "name" or "title" as the entity name (SDLC types use title)
                name = (ent.get("name") or ent.get("title", "")).strip()
                etype = ent.get("type", "Entity").strip()
                if not name:
                    continue
                key = f"{etype}:{name.lower()}"
                # Collect all properties from the LLM response
                extra_props = ent.get("properties", {})
                if isinstance(extra_props, dict):
                    # Also grab top-level keys that aren't metadata
                    for k, v in ent.items():
                        if k not in ("name", "title", "type", "properties", "description") and v:
                            extra_props[k] = v
                if key not in entity_map:
                    entity_map[key] = {
                        "name": name,
                        "type": etype,
                        "description": ent.get("description", ""),
                        "properties": extra_props,
                        "mention_count": 0,
                    }
                else:
                    # Merge properties from subsequent mentions
                    for pk, pv in extra_props.items():
                        if pv and not entity_map[key]["properties"].get(pk):
                            entity_map[key]["properties"][pk] = pv
                entity_map[key]["mention_count"] += 1
                chunk_entity_links.append((chunk["id"], key))

            for rel in result.get("relationships", []):
                src = rel.get("source", "").strip()
                tgt = rel.get("target", "").strip()
                # Accept both "relation" and "type" keys (schema prompt uses "type")
                rtype = (rel.get("type") or rel.get("relation", "RELATED_TO")).strip()
                if src and tgt:
                    all_relationships.append({
                        "source_name": src,
                        "target_name": tgt,
                        "relation": rtype,
                        "properties": rel.get("properties", {}),
                    })

            # Collect facts from schema-guided extraction
            for fact in result.get("facts", []):
                ftype = fact.get("type", "Fact")
                statement = fact.get("statement", "")
                if not statement:
                    continue
                fact_props = {k: v for k, v in fact.items()
                              if k not in ("type",) and v}
                fact_props["extraction_method"] = "llm_extraction"
                fact_props["source"] = ctx.source_url or ctx.source_filename or "llm_extraction"
                all_facts.append({"type": ftype, "properties": fact_props})

        # Build entity nodes with full properties
        name_to_id: Dict[str, str] = {}
        for key, ent in entity_map.items():
            eid = _entity_id(ent["type"], ent["name"])
            name_to_id[ent["name"].lower()] = eid
            props = {
                "name": ent["name"],
                "description": ent.get("description", ""),
                "mention_count": ent["mention_count"],
                "extraction_method": "llm_extraction",
                "source": ctx.source_url or ctx.source_filename or "llm_extraction",
            }
            # Merge in all extracted properties (priority, status, etc.)
            props.update(ent.get("properties", {}))
            ctx.nodes.append({
                "id": eid,
                "label": ent["type"],
                "properties": props,
            })

        # Build chunk → entity MENTIONS edges
        seen_mention = set()
        for chunk_id, entity_key in chunk_entity_links:
            ent = entity_map[entity_key]
            eid = _entity_id(ent["type"], ent["name"])
            edge_key = f"{chunk_id}:{eid}"
            if edge_key in seen_mention:
                continue
            seen_mention.add(edge_key)
            ctx.edges.append({
                "id": str(uuid.uuid4()),
                "source": chunk_id,
                "target": eid,
                "label": "MENTIONS",
                "properties": {},
            })

        # Build relationship edges
        rel_count = 0
        for rel in all_relationships:
            src_id = name_to_id.get(rel["source_name"].lower())
            tgt_id = name_to_id.get(rel["target_name"].lower())
            if src_id and tgt_id and src_id != tgt_id:
                ctx.edges.append({
                    "id": str(uuid.uuid4()),
                    "source": src_id,
                    "target": tgt_id,
                    "label": rel["relation"],
                    "properties": rel.get("properties", {}),
                })
                rel_count += 1

        # Build fact nodes + link to mentioning chunks
        fact_count = 0
        for fact in all_facts:
            fid = f"fact_{hashlib.sha256(fact['properties'].get('statement', str(uuid.uuid4())).encode()).hexdigest()[:12]}"
            ctx.nodes.append({
                "id": fid,
                "label": fact["type"],
                "properties": fact["properties"],
            })
            fact_count += 1

        return {
            "entities": len(entity_map),
            "relationships": rel_count,
            "facts": fact_count,
            "chunks_processed": len(chunks),
        }

    def _extract_from_chunk(
        self, llm, model: str, text: str, schema=None
    ) -> Dict[str, Any]:
        """Extract entities and relationships from a single chunk via LLM."""

        # Use schema_to_prompt if we have an ExtractionSchema object
        try:
            from ..extraction.schema_loader import ExtractionSchema, schema_to_prompt
            if isinstance(schema, ExtractionSchema):
                prompt = schema_to_prompt(schema) + f"\n{text[:3000]}"
                try:
                    raw = llm.generate(prompt=prompt, max_tokens=4000)
                    if raw and raw.strip():
                        text_to_parse = raw.strip()
                        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text_to_parse)
                        if json_match:
                            text_to_parse = json_match.group(1).strip()
                        data = json.loads(text_to_parse)
                        return {
                            "entities": data.get("entities", []),
                            "relationships": data.get("relationships", []),
                            "facts": data.get("facts", []),
                        }
                except Exception:
                    pass
                return {"entities": [], "relationships": [], "facts": []}
        except ImportError:
            pass

        schema_hint = ""
        if schema:
            node_types = list(schema.get("node_types", {}).keys()) if isinstance(schema, dict) else []
            edge_types = list(schema.get("edge_types", {}).keys()) if isinstance(schema, dict) else []
            if node_types:
                schema_hint += f"\nAllowed entity types: {', '.join(node_types)}"
            if edge_types:
                schema_hint += f"\nAllowed relationship types: {', '.join(edge_types)}"
            schema_hint += "\nOnly extract entities and relationships matching these types.\n"

        prompt = f"""Extract all named entities and relationships from the text below.
Return ONLY valid JSON with this exact structure:
{{
  "entities": [
    {{"name": "entity name", "type": "Person|Organization|Location|Concept|Technology|Event|Other", "description": "brief description"}}
  ],
  "relationships": [
    {{"source": "entity name", "target": "entity name", "relation": "WORKS_FOR|LOCATED_IN|RELATED_TO|PART_OF|CREATED_BY|USES|etc", "properties": {{}}}}
  ]
}}
{schema_hint}
Rules:
- Extract ALL meaningful entities (people, organizations, places, concepts, technologies, dates, events)
- Extract relationships between the entities you found
- Use consistent entity names
- Return empty lists if no entities/relationships found
- Return ONLY the JSON, no other text

Text:
{text[:3000]}"""

        for attempt in range(3):
            try:
                raw = llm.generate(prompt=prompt, max_tokens=4000)
                if not raw or not raw.strip():
                    return {"entities": [], "relationships": []}

                # Strip markdown fences if present
                text_to_parse = raw.strip()
                json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text_to_parse)
                if json_match:
                    text_to_parse = json_match.group(1).strip()

                data = json.loads(text_to_parse)
                entities = data.get("entities", [])
                relationships = data.get("relationships", [])
                if not isinstance(entities, list):
                    entities = []
                if not isinstance(relationships, list):
                    relationships = []
                return {"entities": entities, "relationships": relationships}

            except json.JSONDecodeError:
                return {"entities": [], "relationships": []}
            except Exception as e:
                # Retry on rate limit / transient errors
                err_str = str(e).lower()
                if attempt < 2 and ("rate" in err_str or "429" in err_str or "limit" in err_str or "timeout" in err_str):
                    import time
                    wait = (attempt + 1) * 5
                    logger.info("Rate limited on extract (attempt %d), retrying in %ds", attempt + 1, wait)
                    time.sleep(wait)
                    continue
                logger.warning("Extraction failed for chunk: %s", e)
                return {"entities": [], "relationships": []}

    # ------------------------------------------------------------------
    # EXTRACT_FACTS — decompose passages into atomic factual statements
    # ------------------------------------------------------------------

    def _stage_extract_facts(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Extract atomic facts from text chunks using LLM.

        Each fact is a self-contained statement that can be independently
        verified or searched.  Facts bridge passages and entities:
        Passage -[STATES]-> Fact -[MENTIONS]-> Entity.
        """
        llm_model = ctx.pipeline_params.get("llm_model")
        llm = self._get_llm(llm_model)

        if not llm or not llm_model:
            return {"facts": 0, "reason": "no_llm_configured"}

        chunks = ctx.chunks
        if not chunks:
            return {"facts": 0, "reason": "no_chunks"}

        fact_map: Dict[str, Dict] = {}   # canonical_key -> fact dict
        chunk_fact_links: List[tuple] = []  # (chunk_id, fact_key)

        total = len(chunks)
        for i, chunk in enumerate(chunks):
            text = chunk.get("text", "")
            if not text.strip():
                continue

            self._emit_sub_step(ctx, f"Extracting facts from chunk {i+1}/{total}", (i + 1) / total)

            result = self._extract_facts_from_chunk(llm, llm_model, text)

            for fact in result.get("facts", []):
                statement = fact.get("statement", "").strip()
                if not statement or len(statement) < 10:
                    continue
                ftype = fact.get("type", "fact").strip().lower()
                confidence = float(fact.get("confidence", 0.8))

                # Canonical key for dedup
                key = f"fact:{statement.lower()[:120]}"
                if key not in fact_map:
                    fid = f"fact_{hashlib.sha256(key.encode()).hexdigest()[:12]}"
                    fact_map[key] = {
                        "id": fid,
                        "statement": statement,
                        "type": ftype,
                        "confidence": confidence,
                        "mention_count": 0,
                        "entity_names": fact.get("entities", []),
                    }
                fact_map[key]["mention_count"] += 1
                chunk_fact_links.append((chunk["id"], key))

        # Store facts on PipelineContext for later stages
        ctx.facts = []
        for key, f in fact_map.items():
            ctx.facts.append(f)

        # Also create chunk→fact links for PERSIST to use
        ctx.pipeline_params["_chunk_fact_links"] = chunk_fact_links
        ctx.pipeline_params["_fact_keys"] = {k: v["id"] for k, v in fact_map.items()}

        self._emit_sub_step(ctx, f"Extracted {len(fact_map)} facts from {total} chunks")

        return {
            "facts": len(fact_map),
            "chunks_processed": total,
        }

    def _extract_facts_from_chunk(
        self, llm, model: str, text: str
    ) -> Dict[str, Any]:
        """Extract atomic facts from a single chunk via LLM."""
        prompt = f"""Decompose the following text into atomic factual statements.
Each fact should be a single, self-contained sentence that can be independently verified.
Also list which named entities each fact mentions.

Return ONLY valid JSON:
{{
  "facts": [
    {{
      "statement": "Alice joined Acme Corp as CTO in 2024.",
      "type": "claim",
      "confidence": 0.95,
      "entities": ["Alice", "Acme Corp"]
    }}
  ]
}}

Rules:
- Each fact must be one clear, atomic statement
- Type is one of: claim, evidence, definition, event, relationship
- Confidence 0.0-1.0 based on how clearly the text states the fact
- entities: list the exact entity names mentioned in that fact
- Skip trivial/filler statements
- Return ONLY the JSON

Text:
{text[:3000]}"""

        for attempt in range(3):
            try:
                raw = llm.generate(prompt=prompt, max_tokens=4000)
                if not raw or not raw.strip():
                    logger.warning("Fact extraction: LLM returned empty response (attempt %d)", attempt + 1)
                    return {"facts": []}

                text_to_parse = raw.strip()
                json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text_to_parse)
                if json_match:
                    text_to_parse = json_match.group(1).strip()

                # Try to find JSON object if response has extra text
                if not text_to_parse.startswith("{"):
                    brace_match = re.search(r'\{[\s\S]*\}', text_to_parse)
                    if brace_match:
                        text_to_parse = brace_match.group(0)

                data = json.loads(text_to_parse)
                facts = data.get("facts", [])
                if not isinstance(facts, list):
                    facts = []
                logger.info("Fact extraction: got %d facts from chunk", len(facts))
                return {"facts": facts}

            except json.JSONDecodeError as jde:
                logger.warning("Fact extraction: JSON parse error (attempt %d): %s -- raw[:200]: %s",
                               attempt + 1, jde, text_to_parse[:200] if text_to_parse else "empty")
                if attempt < 2:
                    continue
                return {"facts": []}
            except Exception as e:
                err_str = str(e).lower()
                if attempt < 2 and ("rate" in err_str or "429" in err_str or "limit" in err_str or "timeout" in err_str):
                    import time
                    wait = (attempt + 1) * 5
                    logger.info("Rate limited on fact extraction (attempt %d), retrying in %ds", attempt + 1, wait)
                    time.sleep(wait)
                    continue
                logger.warning("Fact extraction failed for chunk: %s", e)
                return {"facts": []}

    # ------------------------------------------------------------------
    # INDEX_BM25 — index facts into BM25 sparse search
    # ------------------------------------------------------------------

    def _stage_index_bm25(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Index extracted facts into the BM25/Whoosh search engine.

        Each fact gets a BM25 index entry.  The fact's graph node stores
        a ``bm25_ref`` pointing to the indexed document ID.
        """
        if not ctx.facts:
            return {"indexed": 0, "reason": "no_facts"}

        try:
            from ..search.whoosh_search import WhooshSearchEngine, WhooshConfig
        except ImportError:
            return {"indexed": 0, "reason": "whoosh_not_available"}

        graph_ns = ctx.graph_namespace or "default"
        # Sanitize namespace for use as directory name (Windows forbids ':' in paths)
        safe_ns = graph_ns.replace(":", "_")
        index_dir = os.path.join("contextcore_data", "bm25_index", safe_ns)
        engine = WhooshSearchEngine(WhooshConfig(index_dir=index_dir))

        count = 0
        for fact in ctx.facts:
            fid = fact["id"]
            engine.index_node(
                node_id=fid,
                label="Fact",
                properties={
                    "name": fact["statement"],
                    "statement": fact["statement"],
                    "type": fact["type"],
                    "confidence": fact["confidence"],
                },
            )
            # Store the BM25 reference back on the fact
            fact["bm25_ref"] = f"bm25://{graph_ns}/{fid}"
            count += 1

        ctx.bm25_indexed = count
        self._emit_sub_step(ctx, f"BM25-indexed {count} facts (backend={engine._backend})")

        # Keep engine reference for potential later querying
        ctx.pipeline_params["_bm25_engine"] = engine

        # Update BM25Index pointer node in graph
        try:
            if graph_ns and self.graph_registry:
                db = self.graph_registry.get_graph(graph_ns)
                if db:
                    for n in db.get_all_nodes():
                        if getattr(n, "label", "") == "BM25Index":
                            n.properties["status"] = "active"
                            n.properties["count"] = count
                            n.properties["backend"] = engine._backend
                            n.properties["index_path"] = index_dir
                            db.add_node(n, write_through=True)
                            break
        except Exception:
            pass

        # Also build comprehensive indexes (keyword + full BM25 + context state)
        # This indexes ALL node types, not just facts
        try:
            from ..core.graph_intelligence import build_indexes
            graph_db = None
            if self.graph_registry:
                graph_db = self.graph_registry.get_graph_for_request(graph_ns)
            if graph_db:
                idx_result = build_indexes(graph_db, graph_ns)
                self._emit_sub_step(ctx, f"Full index build: {idx_result.get('total_ms', '?')}ms")
        except Exception as e:
            logger.debug("[INDEX_BM25] Full index build failed: %s", e)

        return {"indexed": count, "backend": engine._backend}

    # ------------------------------------------------------------------
    # STORE_VECTORS — store passage embeddings in vector DB
    # ------------------------------------------------------------------

    def _stage_store_vectors(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Store passage chunk embeddings in the vector DB.

        The full passage text is stored as vector metadata.  The graph
        node keeps only a ``vector_ref`` pointer and a content preview.
        """
        if not ctx.chunks:
            return {"stored": 0, "reason": "no_chunks"}

        # Only process chunks that have embeddings
        # Collect embedded items from chunks AND nodes
        embedded_chunks = [c for c in ctx.chunks if c.get("embedding")]
        embedded_nodes = [n for n in ctx.nodes if n.get("properties", {}).get("embedding")]
        # Convert nodes to chunk-like format for storage
        for node in embedded_nodes:
            props = node.get("properties", {})
            embedded_chunks.append({
                "id": node.get("id", ""),
                "embedding": props["embedding"],
                "text": props.get("content") or props.get("description") or props.get("name", ""),
                "label": node.get("label", ""),
            })
        if not embedded_chunks:
            return {"stored": 0, "reason": "no_embeddings_on_chunks_or_nodes"}

        try:
            from ..vector.vector_db_manager import get_vector_db_manager
        except ImportError:
            return {"stored": 0, "reason": "vector_db_not_available"}

        graph_ns = ctx.graph_namespace or "default"

        try:
            # Determine embedding dimension from first chunk
            sample_emb = embedded_chunks[0]["embedding"]
            if hasattr(sample_emb, "tolist"):
                sample_emb = sample_emb.tolist()
            dim = len(sample_emb)

            manager = get_vector_db_manager()
            safe_ns = graph_ns.replace(":", "_")
            collection = f"{safe_ns}_passages"
            store = manager.create_store(
                dimension=dim, metric="cosine",
                collection_name=collection,
            )

            # Batch all embeddings for a single add_vectors call
            ids = []
            vectors = []
            metadatas = []
            for chunk in embedded_chunks:
                cid = chunk["id"]
                embedding = chunk["embedding"]
                if hasattr(embedding, "tolist"):
                    embedding = embedding.tolist()
                ids.append(cid)
                vectors.append(embedding)
                metadatas.append({
                    "text": chunk["text"],
                    "chunk_index": chunk.get("index", 0),
                    "char_count": chunk.get("char_count", len(chunk["text"])),
                })

            store.add_vectors(node_ids=ids, vectors=vectors, metadata_list=metadatas)

            # Persist vector store to disk (namespace directory)
            try:
                from pathlib import Path
                vec_dir = Path(f"contextcore_data/namespaces/{graph_ns}/vectors")
                vec_dir.mkdir(parents=True, exist_ok=True)
                store.save(str(vec_dir / f"{collection}.npz"))
                logger.info("Persisted %d vectors to %s", len(ids), vec_dir / f"{collection}.npz")
            except Exception as save_err:
                logger.warning("Failed to persist vectors: %s", save_err)

            # Replace full text on chunks with preview + ref
            for chunk in embedded_chunks:
                chunk["vector_ref"] = f"vec://{collection}/{chunk['id']}"
                chunk["content_preview"] = chunk["text"][:200]

            ctx.vectors_stored = len(ids)
            self._emit_sub_step(ctx, f"Stored {len(ids)} passage embeddings in vector DB")

            # Update VectorIndex pointer node in graph
            try:
                graph_ns = getattr(ctx, "graph_namespace", "") or getattr(ctx, "namespace", "")
                if graph_ns and self.graph_registry:
                    db = self.graph_registry.get_graph(graph_ns)
                    if db:
                        for n in db.get_all_nodes():
                            if getattr(n, "label", "") == "VectorIndex":
                                n.properties["status"] = "active"
                                n.properties["count"] = len(ids)
                                n.properties["collection"] = collection
                                n.properties["embedding_model"] = ctx.pipeline_params.get("_resolved_embedding_model", "")
                                db.add_node(n, write_through=True)
                                break
            except Exception:
                pass

            return {"stored": len(ids), "collection": collection}

        except Exception as e:
            logger.warning("Vector store failed: %s", e)
            # Non-fatal: embeddings stay on chunk nodes as fallback
            return {"stored": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # EMBED — generate embeddings for nodes and chunks
    # ------------------------------------------------------------------

    def _stage_embed(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Embed chunks and content-rich nodes for vector search.

        Uses batched embedding (embed_batch) when available — sends up to
        32 texts per API call instead of 1, reducing API calls by 30x.
        """
        embedding_model = ctx.pipeline_params.get("embedding_model")
        emb = self._get_embeddings(embedding_model)

        if not emb or not embedding_model:
            return {"embedded": 0, "reason": "no_embedding_configured"}

        BATCH_SIZE = 32
        has_batch = hasattr(emb, "embed_batch")

        # Collect all texts to embed
        items = []  # list of (target_dict, key_for_embedding, text)

        # 1. Chunks
        for chunk in ctx.chunks:
            text = (chunk.get("text", "") or chunk.get("content", ""))[:8000]
            if text.strip():
                items.append((chunk, "embedding", text, True))  # True = is_chunk

        # 2. Content-rich nodes
        embeddable_labels = {"Passage", "TextChunk", "Fact", "Feature", "Requirement",
                             "Knowledge", "Finding", "Insight", "Decision", "Document"}
        for node in ctx.nodes:
            props = node.get("properties", {})
            label = node.get("label", "")
            if props.get("embedding"):
                continue
            text = ""
            if label in embeddable_labels:
                text = (props.get("content") or props.get("description") or
                        props.get("statement") or props.get("name", ""))
            elif props.get("content") or props.get("description"):
                text = props.get("content") or props.get("description", "")
            text = (text or "")[:8000]
            if text.strip() and len(text.strip()) >= 20:
                items.append((props, "embedding", text, False))

        if not items:
            return {"embedded": 0, "reason": "nothing_to_embed"}

        self._emit_sub_step(ctx, f"Embedding {len(items)} items (batch_size={BATCH_SIZE})")
        count = 0
        api_calls = 0

        if has_batch:
            # Batched embedding — much faster
            for i in range(0, len(items), BATCH_SIZE):
                batch = items[i:i + BATCH_SIZE]
                texts = [item[2] for item in batch]
                try:
                    resp = emb.embed_batch(texts, model_name=embedding_model)
                    api_calls += 1
                    embeddings = resp.get("embeddings", [])
                    dim = resp.get("dimension", 0)
                    for j, emb_vec in enumerate(embeddings):
                        if emb_vec and j < len(batch):
                            target, key, _, is_chunk = batch[j]
                            target[key] = emb_vec
                            target["embedding_model"] = embedding_model
                            if is_chunk:
                                target["embedding_dim"] = dim or len(emb_vec)
                            count += 1
                except Exception as e:
                    logger.warning("Batch embed failed (batch %d): %s", i // BATCH_SIZE, e)
                    # Fall back to individual for this batch
                    for target, key, text, is_chunk in batch:
                        try:
                            resp = emb.embed_text(text, model_name=embedding_model)
                            api_calls += 1
                            if resp.get("success") and resp.get("embedding"):
                                target[key] = resp["embedding"]
                                target["embedding_model"] = embedding_model
                                if is_chunk:
                                    target["embedding_dim"] = resp.get("dimension", len(resp["embedding"]))
                                count += 1
                        except Exception:
                            pass
        else:
            # Individual embedding (legacy fallback)
            for target, key, text, is_chunk in items:
                try:
                    resp = emb.embed_text(text, model_name=embedding_model)
                    api_calls += 1
                    if resp.get("success") and resp.get("embedding"):
                        target[key] = resp["embedding"]
                        target["embedding_model"] = embedding_model
                        if is_chunk:
                            target["embedding_dim"] = resp.get("dimension", len(resp["embedding"]))
                        count += 1
                except Exception as e:
                    logger.warning("Failed to embed: %s", e)

        ctx.embeddings_count = count
        if count > 0:
            ctx.pipeline_params["_resolved_embedding_model"] = embedding_model
        return {"embedded": count, "api_calls": api_calls, "batch_size": BATCH_SIZE, "embedding_model": embedding_model}

    # ------------------------------------------------------------------
    # CANONICALIZE — deduplicate nodes by name similarity
    # ------------------------------------------------------------------

    def _stage_canonicalize(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Deduplicate nodes by canonical name (cross-label), merge edges."""
        if not ctx.nodes:
            return {"original": 0, "deduped": 0}

        original_count = len(ctx.nodes)
        canonical_map: Dict[str, str] = {}  # name_lower -> chosen node id
        deduped_nodes: Dict[str, Dict] = {}  # node_id -> node dict
        id_remap: Dict[str, str] = {}  # old_id -> canonical_id

        entity_labels = {"Person", "Organization", "Location", "Event", "Entity"}

        for node in ctx.nodes:
            name = node.get("properties", {}).get("name", "")
            label = node.get("label", "Entity")

            # Non-entity nodes pass through unchanged
            if label not in entity_labels:
                deduped_nodes[node["id"]] = node
                id_remap[node["id"]] = node["id"]
                continue

            # Canonical key: name only (allows cross-label merge like "Iran" Location + Organization)
            canonical_key = name.strip().lower()
            if not canonical_key or len(canonical_key) < 2:
                deduped_nodes[node["id"]] = node
                id_remap[node["id"]] = node["id"]
                continue

            if canonical_key in canonical_map:
                existing_id = canonical_map[canonical_key]
                id_remap[node["id"]] = existing_id
                existing = deduped_nodes[existing_id]
                existing_mc = existing.get("properties", {}).get("mention_count", 0) or 0
                node_mc = node.get("properties", {}).get("mention_count", 0) or 0
                existing["properties"]["mention_count"] = existing_mc + node_mc
            else:
                canonical_map[canonical_key] = node["id"]
                deduped_nodes[node["id"]] = node
                id_remap[node["id"]] = node["id"]

        # Remap edge source/target IDs and deduplicate edges
        seen_edges = set()
        deduped_edges = []
        for edge in ctx.edges:
            src = id_remap.get(edge.get("source", ""), edge.get("source", ""))
            tgt = id_remap.get(edge.get("target", ""), edge.get("target", ""))
            if src == tgt:
                continue  # Skip self-loops created by merge
            edge_key = f"{src}:{tgt}:{edge.get('label', '')}"
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            deduped_edges.append({
                **edge,
                "source": src,
                "target": tgt,
            })

        ctx.nodes = list(deduped_nodes.values())
        ctx.edges = deduped_edges

        return {
            "original_nodes": original_count,
            "deduped_nodes": len(ctx.nodes),
            "removed": original_count - len(ctx.nodes),
            "original_edges": len(ctx.edges),
        }

    # ------------------------------------------------------------------
    # ENHANCE_GRAPH — enrich graph with additional relationships
    # ------------------------------------------------------------------

    def _stage_enhance_graph(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Enhance graph by adding inferred edges (co-occurrence, hierarchy)."""
        if not ctx.nodes:
            return {"edges_added": 0}

        edges_added = 0

        # Co-occurrence: if two entities appear in the same chunk, link them
        if ctx.chunks:
            chunk_entities: Dict[str, List[str]] = {}
            for edge in ctx.edges:
                if edge.get("label") == "MENTIONS":
                    chunk_id = edge["source"]
                    entity_id = edge["target"]
                    chunk_entities.setdefault(chunk_id, []).append(entity_id)

            seen = set()
            for chunk_id, entities in chunk_entities.items():
                for i, e1 in enumerate(entities):
                    for e2 in entities[i + 1:]:
                        pair = tuple(sorted([e1, e2]))
                        if pair in seen:
                            continue
                        seen.add(pair)
                        ctx.edges.append({
                            "id": str(uuid.uuid4()),
                            "source": e1,
                            "target": e2,
                            "label": "CO_OCCURS_WITH",
                            "properties": {"inferred": True},
                        })
                        edges_added += 1

        return {"edges_added": edges_added}

    # ------------------------------------------------------------------
    # PERSIST — save nodes and edges to the graph (via AIQL)
    # ------------------------------------------------------------------

    def _stage_persist(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Persist the full GraphRAG hierarchy to graph storage via AIQLExecutor.

        Hierarchy created:
            Document -[HAS_PASSAGE]-> Passage -[STATES]-> Fact -[MENTIONS]-> Entity
                                       |                    |
                                    vector_ref           bm25_ref
                                   (vector DB)         (BM25 index)

        Falls back to the simpler Document-[CONTAINS]->TextChunk layout
        when no facts are present (legacy pipelines).

        All graph mutations go through ``AIQLExecutor.bulk_ingest()`` so they
        participate in time-travel versioning, audit, and are consistent with
        AIQL CREATE NODE / CREATE EDGE semantics.
        """
        if not self.graph_registry:
            raise ValueError("No graph_registry configured")

        graph_ns = ctx.graph_namespace
        if not graph_ns:
            raise ValueError("No graph_namespace set on PipelineContext")

        aiql = self._get_aiql_executor()
        if not aiql:
            raise ValueError("Could not initialize AIQLExecutor")

        # Activate the target namespace
        aiql.active_namespace = graph_ns

        # Collect all nodes and edges, then send through AIQL in one batch
        all_nodes: List[Dict[str, Any]] = []
        all_edges: List[Dict[str, Any]] = []
        facts_created = 0

        has_facts = bool(ctx.facts)
        chunk_label = "Passage" if has_facts else "TextChunk"
        chunk_edge_label = "HAS_PASSAGE" if has_facts else "CONTAINS"

        # ---- 1. Document node ----
        doc_id = None
        if ctx.chunks:
            doc_id = str(uuid.uuid4())
            title = ctx.source_filename or "Untitled"
            self._emit_sub_step(ctx, f"Building Document node: {title}")
            all_nodes.append({
                "id": doc_id,
                "label": "Document",
                "properties": {
                    "name": title,
                    "source": ctx.source_url or ctx.source_filename or "upload",
                    "chunk_count": len(ctx.chunks),
                    "fact_count": len(ctx.facts) if has_facts else 0,
                },
            })

            # ---- 2. Passage / TextChunk nodes ----
            self._emit_sub_step(ctx, f"Building {len(ctx.chunks)} {chunk_label} nodes")
            for chunk in ctx.chunks:
                props = {
                    "name": f"{title} ({chunk_label.lower()} {chunk['index'] + 1}/{len(ctx.chunks)})",
                    "char_count": chunk.get("char_count", len(chunk["text"])),
                    "chunk_index": chunk["index"],
                    "document_id": doc_id,
                }
                if chunk.get("vector_ref"):
                    # Text lives in vector store — graph stores only the pointer
                    props["vector_ref"] = chunk["vector_ref"]
                else:
                    # No vector store — keep full text in graph node
                    props["content"] = chunk["text"]

                if "embedding" in chunk and not chunk.get("vector_ref"):
                    props["embedding"] = chunk["embedding"]
                    props["embedding_model"] = chunk.get("embedding_model", "")
                    props["embedding_dim"] = chunk.get("embedding_dim", 0)

                # Dedup hashes (set by DEDUP stage)
                if chunk.get("content_hash"):
                    props["content_hash"] = chunk["content_hash"]
                if chunk.get("normalised_hash"):
                    props["normalised_hash"] = chunk["normalised_hash"]
                if chunk.get("simhash"):
                    props["simhash"] = chunk["simhash"]

                all_nodes.append({"id": chunk["id"], "label": chunk_label, "properties": props})

                # Document → Passage edge
                all_edges.append({
                    "id": str(uuid.uuid4()),
                    "source": doc_id,
                    "target": chunk["id"],
                    "label": chunk_edge_label,
                    "properties": {"chunk_index": chunk["index"]},
                })

        # ---- 3. Fact nodes + Passage→Fact edges ----
        if has_facts:
            self._emit_sub_step(ctx, f"Building {len(ctx.facts)} Fact nodes")
            chunk_fact_links = ctx.pipeline_params.get("_chunk_fact_links", [])
            fact_keys = ctx.pipeline_params.get("_fact_keys", {})

            for fact in ctx.facts:
                fid = fact["id"]
                fprops = {
                    "name": fact["statement"][:80],
                    "statement": fact["statement"],
                    "fact_type": fact["type"],
                    "confidence": fact["confidence"],
                    "mention_count": fact.get("mention_count", 1),
                    "extraction_method": "llm_extraction",
                    "source": ctx.source_url or ctx.source_filename or "llm_extraction",
                }
                if fact.get("bm25_ref"):
                    fprops["bm25_ref"] = fact["bm25_ref"]

                all_nodes.append({"id": fid, "label": "Fact", "properties": fprops})
                facts_created += 1

            # Passage → Fact STATES edges (deduplicated)
            seen_states = set()
            for chunk_id, fact_key in chunk_fact_links:
                fid = fact_keys.get(fact_key)
                if not fid:
                    continue
                edge_key = f"{chunk_id}:{fid}"
                if edge_key in seen_states:
                    continue
                seen_states.add(edge_key)
                all_edges.append({
                    "id": str(uuid.uuid4()),
                    "source": chunk_id,
                    "target": fid,
                    "label": "STATES",
                    "properties": {},
                })

            # Fact → Entity MENTIONS edges
            entity_name_to_id = {}
            for node in ctx.nodes:
                ename = node.get("properties", {}).get("name", "").lower()
                if ename:
                    entity_name_to_id[ename] = node["id"]

            seen_fact_ent = set()
            for fact in ctx.facts:
                fid = fact["id"]
                for ename in fact.get("entity_names", []):
                    eid = entity_name_to_id.get(ename.lower())
                    if not eid:
                        continue
                    edge_key = f"{fid}:{eid}"
                    if edge_key in seen_fact_ent:
                        continue
                    seen_fact_ent.add(edge_key)
                    all_edges.append({
                        "id": str(uuid.uuid4()),
                        "source": fid,
                        "target": eid,
                        "label": "MENTIONS",
                        "properties": {},
                    })

        # ---- 4. Entity / tabular nodes ----
        if ctx.nodes:
            self._emit_sub_step(ctx, f"Building {len(ctx.nodes)} entity nodes")
        all_nodes.extend(ctx.nodes)

        # ---- 5. Relationship / other edges ----
        all_edges.extend(ctx.edges)

        # ---- 5b. Tag category + IN_CATEGORY edges ----
        category_boundary_id = ctx.pipeline_params.get("_category_boundary_id")
        if category_boundary_id:
            from ..context.boundaries import tag_nodes_with_category, build_category_edges, BOUNDARY_NODE_LABELS
            tag_nodes_with_category(all_nodes, "knowledge_base")
            cat_edges = build_category_edges(all_nodes, category_boundary_id)
            all_edges.extend(cat_edges)
            self._emit_sub_step(ctx, f"Tagged {len(all_nodes)} nodes as knowledge_base, {len(cat_edges)} IN_CATEGORY edges")

        # ---- 6. Bulk ingest through AIQL ----
        self._emit_sub_step(ctx, f"Persisting via AIQL: {len(all_nodes)} nodes, {len(all_edges)} edges")

        def _on_progress(msg, prog):
            self._emit_sub_step(ctx, msg, prog)

        result = aiql.bulk_ingest(
            namespace=graph_ns,
            nodes=all_nodes,
            edges=all_edges,
            merge_existing=True,
            on_progress=_on_progress,
        )

        nodes_created = result.get("nodes_created", 0)
        nodes_merged = result.get("nodes_merged", 0)
        edges_created = result.get("edges_created", 0)
        errors = result.get("errors", [])

        # ---- 7. Store embedding model on graph metadata ----
        emb_model = ctx.pipeline_params.get("_resolved_embedding_model") or ctx.pipeline_params.get("embedding_model")
        if emb_model and self.graph_registry:
            meta = self.graph_registry.metadata.get(graph_ns)
            if meta:
                meta.embedding_model = emb_model
            try:
                self.graph_registry._save_metadata()
            except Exception:
                pass

        self._emit_sub_step(ctx, f"Saved: {nodes_created} created, {nodes_merged} merged, {edges_created} edges")

        # Build search indexes (keyword, BM25, context state) in background
        # so they're ready before any agent searches
        import threading
        def _post_persist_index_build():
            try:
                from ..core.graph_intelligence import build_indexes
                graph_db = aiql._graph if hasattr(aiql, '_graph') else None
                if not graph_db and self.graph_registry:
                    graph_db = self.graph_registry.get_graph_for_request(graph_ns)
                if graph_db:
                    result = build_indexes(graph_db, graph_ns)
                    logger.info("[PERSIST] Post-ingest index build: %s", result)
            except Exception as e:
                logger.debug("[PERSIST] Index build failed: %s", e)

        t = threading.Thread(target=_post_persist_index_build, daemon=True, name=f"index-{graph_ns[:12]}")
        t.start()
        self._emit_sub_step(ctx, "Index build started (background)")

        return {
            "nodes_created": nodes_created,
            "nodes_merged": nodes_merged,
            "edges_created": edges_created,
            "facts_created": facts_created,
            "errors": errors[:10] if errors else [],
        }

    # ------------------------------------------------------------------
    # VALIDATE_SCHEMA — validate nodes/edges against a GraphSchema
    # ------------------------------------------------------------------

    def _stage_validate_schema(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Validate extracted nodes/edges against a GraphSchema definition."""
        schema_yaml = ctx.pipeline_params.get("schema_yaml", "")
        if not schema_yaml:
            return {"skipped": True, "reason": "no_schema_provided"}

        try:
            import yaml
            from ..schema.schema_parser import SchemaParser
            from .schema_validator import SchemaValidator

            # Parse schema from YAML string
            import tempfile, os
            fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix="schema_")
            os.close(fd)
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    f.write(schema_yaml)
                parser = SchemaParser(tmp)
                schema = parser.parse()
            finally:
                os.unlink(tmp)

            strict = ctx.pipeline_params.get("schema_strict", False)
            validator = SchemaValidator(schema)
            result = validator.validate(ctx.nodes, ctx.edges, strict=strict)

            # Replace context nodes/edges with validated ones
            ctx.nodes = result.valid_nodes
            ctx.edges = result.valid_edges

            return result.to_dict()

        except Exception as e:
            logger.warning("Schema validation failed: %s", e)
            return {"error": str(e), "nodes_kept": len(ctx.nodes), "edges_kept": len(ctx.edges)}

    # ------------------------------------------------------------------
    # IMPORT_GRAPH — import graph files (GraphML, JSON, RDF)
    # ------------------------------------------------------------------

    def _stage_import_graph(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Import a graph interchange file (GraphML, RDF/Turtle, JSON-LD, plain JSON)."""
        from .parsers import GraphParserRegistry

        pages = (ctx.extraction_result or {}).get("pages", [])
        if not pages:
            return {"nodes": 0, "edges": 0, "reason": "no_content"}

        text = pages[0].get("text", "")
        if not text.strip():
            return {"nodes": 0, "edges": 0, "reason": "empty_content"}

        filename = ctx.source_filename or ""

        try:
            registry = GraphParserRegistry()
            parsed_nodes, parsed_edges = registry.parse(text, filename=filename)

            ctx.nodes.extend(parsed_nodes)
            ctx.edges.extend(parsed_edges)

            return {
                "nodes": len(parsed_nodes),
                "edges": len(parsed_edges),
                "format": filename.rsplit(".", 1)[-1] if "." in filename else "unknown",
            }
        except Exception as e:
            logger.warning("Graph import failed: %s", e)
            return {"nodes": 0, "edges": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # SDLC_SCAN — scan a repo directory into SDLC-typed graph nodes
    # ------------------------------------------------------------------

    def _stage_sdlc_scan(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Scan a repo directory into SDLC-typed graph nodes."""
        from .universal.operators.sdlc_scan import SDLCScanOperator
        from .universal.operators.index_bm25 import IndexBM25Operator
        from .universal.operators.embed import EmbedOperator
        from .universal.ingest_content import Chunk
        from .universal.stage_executor import GraphContext as UniversalGraphContext

        repo_path = ctx.source_text or ctx.source_url or ""
        if not repo_path:
            return {"nodes": 0, "edges": 0, "reason": "no_repo_path"}

        # Get or create graph for this namespace
        graph_ns = ctx.graph_namespace or "default"
        db = None
        if self.graph_registry:
            db = self.graph_registry.get_graph(graph_ns)
            if db is None:
                db = self.graph_registry.create_graph(graph_ns)

        if db is None:
            return {"nodes": 0, "edges": 0, "reason": "no_graph"}

        graph_ctx = UniversalGraphContext(db=db, namespace=graph_ns)

        # Pass config + source URL through chunk metadata
        chunk_meta = {"repo_path": repo_path}
        if ctx.source_url:
            chunk_meta["source_url"] = ctx.source_url
        if ctx.pipeline_params:
            chunk_meta["pipeline_params"] = ctx.pipeline_params
        chunks = [Chunk(content="", index=0, metadata=chunk_meta)]

        op = SDLCScanOperator(config=ctx.pipeline_params or {})
        op.process(chunks, graph_ctx)

        # Build full node dicts for PipelineContext (needed by downstream consumers)
        full_nodes = []
        for nid in graph_ctx.node_ids:
            node = graph_ctx.get_node(nid)
            if node is not None:
                full_nodes.append({
                    "id": nid,
                    "label": node.label,
                    "properties": dict(node.properties),
                })
            else:
                full_nodes.append({"id": nid, "label": "", "properties": {}})
        ctx.nodes = full_nodes

        # Edges are already committed to the graph — no need to track in ctx
        ctx.edges = []

        # Run BM25 indexing and embedding inline (nodes already in graph)
        try:
            IndexBM25Operator().process([], graph_ctx)
        except Exception as exc:
            logger.debug("[SDLC_SCAN] BM25 indexing skipped: %s", exc)

        try:
            EmbedOperator().process([], graph_ctx)
        except Exception as exc:
            logger.debug("[SDLC_SCAN] Embed skipped: %s", exc)

        # Build Context Units from SDLC nodes
        try:
            from .universal.operators.synthesize_cu import SynthesizeCUOperator
            # max_cus configurable via pipeline_params, default 8
            _max_cus = (ctx.pipeline_params or {}).get("max_cus", 8)
            SynthesizeCUOperator(max_cus=_max_cus).process([], graph_ctx)
        except Exception as exc:
            logger.debug("[SDLC_SCAN] CU synthesis skipped: %s", exc)

        return {
            "nodes": len(graph_ctx.node_ids),
            "edges": graph_ctx.edge_count,
            "errors": graph_ctx.errors,
        }

    # ------------------------------------------------------------------
    # SDLC sub-stages (resumable pipeline)
    # ------------------------------------------------------------------

    def _get_sdlc_graph_ctx(self, ctx: PipelineContext):
        """Get or create graph context for SDLC sub-stages."""
        from .universal.stage_executor import GraphContext as UniversalGraphContext
        graph_ns = ctx.graph_namespace or "default"
        db = None
        if self.graph_registry:
            db = self.graph_registry.get_graph(graph_ns)
            if db is None:
                db = self.graph_registry.create_graph(graph_ns)
        if db is None:
            return None, graph_ns
        return UniversalGraphContext(db=db, namespace=graph_ns), graph_ns

    def _stage_sdlc_scan_files(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Sub-stage 1: Scan repo files — README, source, tests, docs, API routes."""
        graph_ctx, graph_ns = self._get_sdlc_graph_ctx(ctx)
        if graph_ctx is None:
            return {"nodes": 0, "reason": "no_graph"}

        repo_path = ctx.source_text or ""
        if not repo_path or not os.path.isdir(repo_path):
            return {"nodes": 0, "reason": "no_repo_path"}

        from .universal.operators.scanners.repo import (
            scan_readme, scan_source_modules, scan_tests, scan_docs, scan_api_routes,
        )
        from .universal.operators.scanners.llm_enrichment import scan_with_llm
        from .universal.operators.scanners.jira import scan_jira_issues
        from pathlib import Path

        rp = Path(repo_path)
        all_nodes = (
            scan_readme(rp) + scan_source_modules(rp) + scan_tests(rp)
            + scan_docs(rp) + scan_api_routes(rp)
        )

        # Optional LLM enrichment
        skip_llm = (ctx.pipeline_params or {}).get("skip_llm", True) or os.environ.get("SDLC_SKIP_LLM")
        if not skip_llm:
            code = [n for n in all_nodes if n["label"] == "CodeModule"]
            non_code = [n for n in all_nodes if n["label"] != "CodeModule"]
            all_nodes = non_code + scan_with_llm(rp, code)

        # Jira (API-based, no clone needed)
        all_nodes += scan_jira_issues()

        for n in all_nodes:
            graph_ctx.add_node(label=n["label"], properties=dict(n.get("properties", {})), node_id=n["id"])

        # Store node defs in ctx for edge inference later
        ctx.nodes = [{"id": n["id"], "label": n["label"], "properties": n.get("properties", {})} for n in all_nodes]
        return {"nodes": len(all_nodes)}

    def _stage_sdlc_scan_github(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Sub-stage 2: Fetch GitHub issues, PRs, contributors via API."""
        graph_ctx, _ = self._get_sdlc_graph_ctx(ctx)
        if graph_ctx is None:
            return {"nodes": 0, "reason": "no_graph"}

        source_url = ctx.source_url or ctx.source_text or ""
        repo_path = ctx.source_text or "."

        from .universal.operators.scanners.github import scan_github_issues_full
        from .universal.operators.scanners.github_api import quick_scan
        from pathlib import Path

        token = getattr(ctx, "github_token", None) or None
        # Try full GitHub scan (issues + PRs + contributors + metadata)
        nodes = (
            quick_scan(source_url, github_token=token)
            if source_url.startswith("https://")
            else scan_github_issues_full(Path(repo_path), source_url, github_token=token)
        )

        for n in nodes:
            graph_ctx.add_node(label=n["label"], properties=dict(n.get("properties", {})), node_id=n["id"])

        ctx.nodes = (ctx.nodes or []) + [{"id": n["id"], "label": n["label"], "properties": n.get("properties", {})} for n in nodes]
        return {"nodes": len(nodes)}

    def _stage_sdlc_scan_git(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Sub-stage 3: Git history + repo metadata."""
        graph_ctx, _ = self._get_sdlc_graph_ctx(ctx)
        if graph_ctx is None:
            return {"nodes": 0, "reason": "no_graph"}

        repo_path = ctx.source_text or ""
        if not repo_path or not os.path.isdir(repo_path):
            return {"nodes": 0, "reason": "no_repo_path"}

        from .universal.operators.scanners.git import scan_git_history, scan_repo_metadata
        from pathlib import Path

        rp = Path(repo_path)
        meta_nodes = scan_repo_metadata(rp)
        history_nodes, history_edges = scan_git_history(rp)
        all_nodes = meta_nodes + history_nodes

        for n in all_nodes:
            graph_ctx.add_node(label=n["label"], properties=dict(n.get("properties", {})), node_id=n["id"])
        for e in history_edges:
            graph_ctx.add_edge(source_id=e["source"], target_id=e["target"], label=e["label"])

        ctx.nodes = (ctx.nodes or []) + [{"id": n["id"], "label": n["label"], "properties": n.get("properties", {})} for n in all_nodes]
        return {"nodes": len(all_nodes), "edges": len(history_edges)}

    def _stage_sdlc_scan_edges(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Sub-stage 4: Infer edges + BM25 index + embed."""
        graph_ctx, _ = self._get_sdlc_graph_ctx(ctx)
        if graph_ctx is None:
            return {"edges": 0, "reason": "no_graph"}

        from .universal.operators.scanners.edges import infer_edges
        from .universal.operators.index_bm25 import IndexBM25Operator
        from .universal.operators.embed import EmbedOperator

        # Infer edges from all nodes accumulated in ctx.nodes
        edges = infer_edges(ctx.nodes or [])
        for e in edges:
            graph_ctx.add_edge(source_id=e["source"], target_id=e["target"], label=e["label"])

        # BM25 + embed
        # Rebuild node_ids from ctx.nodes since graph_ctx is fresh
        for n in (ctx.nodes or []):
            if n["id"] not in graph_ctx.node_ids:
                graph_ctx.node_ids.append(n["id"])

        try:
            IndexBM25Operator().process([], graph_ctx)
        except Exception:
            pass
        try:
            EmbedOperator().process([], graph_ctx)
        except Exception:
            pass

        return {"edges": len(edges), "indexed": len(graph_ctx.node_ids)}

    def _stage_sdlc_scan_cu(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Sub-stage 5: Build Context Units."""
        graph_ctx, _ = self._get_sdlc_graph_ctx(ctx)
        if graph_ctx is None:
            return {"cus": 0, "reason": "no_graph"}

        from .universal.operators.synthesize_cu import SynthesizeCUOperator

        # Rebuild node_ids
        for n in (ctx.nodes or []):
            if n["id"] not in graph_ctx.node_ids:
                graph_ctx.node_ids.append(n["id"])

        max_cus = (ctx.pipeline_params or {}).get("max_cus", 8)
        SynthesizeCUOperator(max_cus=max_cus).process([], graph_ctx)

        cu_count = sum(1 for nid in graph_ctx.node_ids if nid.startswith("cu_"))
        return {"cus": cu_count}


# ---------------------------------------------------------------------------
# Convenience: create a context and execute a full pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    graph_registry,
    graph_namespace: str,
    stages: List[str],
    source_text: Optional[str] = None,
    source_file_path: Optional[str] = None,
    source_filename: Optional[str] = None,
    intent: str = "graph_rag",
    mode: str = "run_all",
    pipeline_params: Optional[Dict[str, Any]] = None,
    on_stage: Optional[Callable] = None,
) -> PipelineContext:
    """
    Convenience function to create a PipelineContext and execute stages.

    Returns the final PipelineContext with all results.
    """
    ctx = PipelineContext(
        graph_namespace=graph_namespace,
        stages=stages,
        source_text=source_text,
        source_bytes_path=source_file_path,
        source_filename=source_filename,
        intent=intent,
        mode=mode,
        pipeline_params=pipeline_params or {},
    )

    executor = StageExecutor(graph_registry=graph_registry)

    if mode == "step_by_step":
        executor.execute_next(ctx)
    else:
        executor.execute_all(ctx, on_stage=on_stage)

    return ctx
