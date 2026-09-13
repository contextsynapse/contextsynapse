"""Re-process existing contexts through the smart pipeline.

Takes old TextChunk-based contexts and re-runs them through the new
7-stage pipeline to produce Passages + Entities + Facts + Edges.
Always uses review mode — shows before/after comparison before committing.
"""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ReprocessPreview:
    """Before/after comparison for re-processing a context."""
    context_id: str = ""
    staging_id: str = ""
    before: Dict[str, Any] = field(default_factory=dict)
    after: Any = None  # StagingResult
    documents_processed: int = 0
    quality_before: float = 0.0
    quality_after: float = 0.0

    def commit(self, db):
        """Delete old TextChunks, commit new Passages + Entities + Facts."""
        if not self.after:
            return None

        # Delete old TextChunks and their edges
        try:
            all_nodes = db.csr_adapter.get_all_nodes()
            old_chunks = [
                n for n in all_nodes
                if getattr(n, 'node_type', getattr(n, 'label', '')) == 'TextChunk'
            ]
            for chunk in old_chunks:
                try:
                    db.csr_adapter.remove_node(chunk.id)
                except Exception:
                    pass
            logger.info("Deleted %d old TextChunks", len(old_chunks))
        except Exception as e:
            logger.warning("Failed to delete old TextChunks: %s", e)

        # Commit new nodes
        return self.after.commit(db)

    def discard(self):
        """Keep everything as-is."""
        if self.after:
            self.after.discard()

    def to_dict(self) -> Dict:
        return {
            "context_id": self.context_id,
            "staging_id": self.staging_id,
            "before": self.before,
            "after": self.after.to_dict() if self.after else {},
            "documents_processed": self.documents_processed,
            "quality_before": self.quality_before,
            "quality_after": self.quality_after,
        }


def reprocess_context(context_id: str, db, graph_registry=None) -> ReprocessPreview:
    """Re-process an existing context through the smart pipeline.

    Reads existing Documents and TextChunks, re-extracts through
    Clean -> Chunk -> Extract. Returns preview for approval.

    Args:
        context_id: Context ID or graph namespace.
        db: AIContextDB instance for the context's graph.
        graph_registry: Optional, for resolving context namespaces.

    Returns:
        ReprocessPreview with before/after comparison.
    """
    from .smart_ingest import ingest_text, StagingResult, _calc_quality

    preview = ReprocessPreview(
        context_id=context_id,
        staging_id=str(_uuid.uuid4()),
    )

    # Count before state
    all_nodes = db.csr_adapter.get_all_nodes()
    text_chunks = [n for n in all_nodes if getattr(n, 'node_type', getattr(n, 'label', '')) == 'TextChunk']
    documents = [n for n in all_nodes if getattr(n, 'node_type', getattr(n, 'label', '')) == 'Document']
    passages = [n for n in all_nodes if getattr(n, 'node_type', getattr(n, 'label', '')) == 'Passage']
    entities = [n for n in all_nodes if getattr(n, 'node_type', getattr(n, 'label', '')) in
                ('Person', 'Organization', 'Entity', 'Location', 'Event')]
    facts = [n for n in all_nodes if getattr(n, 'node_type', getattr(n, 'label', '')) == 'Fact']

    preview.before = {
        "text_chunks": len(text_chunks),
        "documents": len(documents),
        "passages": len(passages),
        "entities": len(entities),
        "facts": len(facts),
    }
    preview.quality_before = _calc_quality(
        [{"token_count": len(n.properties.get("content", "")) // 4} for n in text_chunks],
        [{"name": n.properties.get("name", "")} for n in entities],
        [{"statement": n.properties.get("statement", "")} for n in facts],
        [],
    )

    # Gather content to re-process
    # Priority 1: re-fetch from source URLs in Document nodes
    # Priority 2: concatenate TextChunk content
    combined_text = ""
    combined_title = ""
    docs_processed = 0

    for doc in documents:
        url = doc.properties.get("url", "")
        title = doc.properties.get("title", doc.properties.get("name", ""))

        if url and url.startswith("http"):
            # Try re-fetching
            try:
                from .cleaner import clean_webpage
                clean_doc = clean_webpage(url)
                if clean_doc.body and len(clean_doc.body) > 50:
                    combined_text += f"\n\n{clean_doc.body}"
                    combined_title = combined_title or clean_doc.title or title
                    docs_processed += 1
                    continue
            except Exception:
                pass

        # Fallback: use TextChunk content from this document
        doc_chunks = [c for c in text_chunks if c.properties.get("document_id") == doc.id
                      or c.properties.get("article_id") == doc.id]
        if doc_chunks:
            chunk_text = "\n\n".join(c.properties.get("content", c.properties.get("text", "")) for c in doc_chunks)
            if chunk_text.strip():
                combined_text += f"\n\n{chunk_text}"
                combined_title = combined_title or title
                docs_processed += 1

    # If no documents, try raw TextChunks
    if not combined_text and text_chunks:
        combined_text = "\n\n".join(
            c.properties.get("content", c.properties.get("text", "")) for c in text_chunks
        )
        combined_title = "Re-processed content"
        docs_processed = len(text_chunks)

    if not combined_text.strip():
        preview.after = StagingResult(staging_id=preview.staging_id)
        preview.documents_processed = 0
        return preview

    # Run through smart pipeline in review mode
    staging = ingest_text(
        combined_text, db,
        title=combined_title,
        mode="review",
        pipeline="smart_article",
    )

    if isinstance(staging, StagingResult):
        preview.after = staging
        preview.quality_after = staging.quality_score
    else:
        # auto mode returned BuildResult (shouldn't happen with mode=review)
        preview.after = StagingResult(staging_id=preview.staging_id)

    preview.documents_processed = docs_processed
    preview.staging_id = staging.staging_id if isinstance(staging, StagingResult) else preview.staging_id

    return preview
