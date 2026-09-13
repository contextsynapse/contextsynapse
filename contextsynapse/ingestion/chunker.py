"""Hybrid paragraph-based chunker.

Splits article text into semantically meaningful passages:
- Paragraph boundaries are the primary split points
- Short paragraphs (<100 tokens) merged with next
- Long paragraphs (>800 tokens) split at sentence boundary
- Overlap: last sentence of previous chunk prepended to next
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Try tiktoken for accurate token counting, fallback to char-based
try:
    import tiktoken
    _enc = tiktoken.get_encoding("cl100k_base")

    def _count_tokens(text: str) -> int:
        return len(_enc.encode(text))
except ImportError:
    def _count_tokens(text: str) -> int:
        return max(1, len(text) // 4)


# Sentence split pattern — handles Mr./Dr./etc. abbreviations
_SENTENCE_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z])')

MIN_TOKENS = 100
MAX_TOKENS = 800
TARGET_TOKENS = 500


@dataclass
class PassageChunk:
    """A semantically meaningful text chunk ready for extraction + embedding."""
    content: str
    overlap_prefix: str = ""       # last sentence from previous chunk
    chunk_index: int = 0
    token_count: int = 0
    char_count: int = 0
    links_in_chunk: List[Dict] = field(default_factory=list)
    section_title: str = ""        # heading of the section this passage belongs to
    section_index: int = 0         # ordinal section number (0 = before first heading)
    heading_path: List[str] = field(default_factory=list)  # full breadcrumb ["H1 title", "H2 title"]
    heading_depth: int = 0                                   # depth of immediate parent heading (0=none, 1=H1, 2=H2…)

    def __post_init__(self):
        if not self.token_count:
            self.token_count = _count_tokens(self.content)
        if not self.char_count:
            self.char_count = len(self.content)


def _heading_depth(para: str) -> int:
    """Return heading depth: 0=not a heading, 1=H1, 2=H2, 3=H3+."""
    line = para.strip()
    if not line or len(line) > 300:
        return 0
    # Markdown: # H1, ## H2, ### H3 (trafilatura markdown output)
    m = re.match(r'^(#{1,6})\s+\S', line)
    if m:
        return len(m.group(1))
    # ALL CAPS short line  (e.g. "KEY HIGHLIGHTS", "FINANCIAL RESULTS")
    if line.isupper() and 2 <= len(line.split()) <= 8:
        return 2
    # Short line ending with colon (e.g. "Revenue breakdown:")
    if line.rstrip().endswith(":") and _count_tokens(line) <= 12:
        return 3
    return 0


def _is_heading(para: str) -> bool:
    return _heading_depth(para) > 0


def _split_sections(paragraphs: List[str]) -> List[tuple]:
    """Group paragraphs into (heading_path, heading_depth, [paragraphs]) tuples.

    heading_path: full breadcrumb list e.g. ["Company Overview", "Financial Highlights"]
    heading_depth: depth of the immediately enclosing heading (1=H1, 2=H2, 3=H3, 0=no heading)

    Headings start a new section. The heading stack tracks ancestor headings so
    each section carries the full path to the root.
    """
    sections: List[tuple] = []
    heading_stack: List[tuple] = []   # list of (depth, title)
    current_paras: List[str] = []

    def _flush():
        if current_paras or not sections:
            path = [title for _, title in heading_stack]
            depth = heading_stack[-1][0] if heading_stack else 0
            sections.append((path, depth, list(current_paras)))
            current_paras.clear()

    for para in paragraphs:
        depth = _heading_depth(para)
        if depth > 0:
            _flush()
            # Pop shallower/equal headings to maintain tree invariant
            while heading_stack and heading_stack[-1][0] >= depth:
                heading_stack.pop()
            title = para.strip().lstrip("#").strip().rstrip(":").strip()
            heading_stack.append((depth, title))
        else:
            current_paras.append(para)

    _flush()

    if not sections:
        return [([], 0, paragraphs)]

    return sections


def _split_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs."""
    # Split on double newlines or single newlines followed by blank lines
    paras = re.split(r'\n\s*\n', text)
    # Also handle single-newline-separated blocks (common in web content)
    result = []
    for p in paras:
        p = p.strip()
        if p:
            result.append(p)
    return result


def _split_sentences(text: str) -> List[str]:
    """Split text into sentences."""
    sentences = _SENTENCE_RE.split(text)
    return [s.strip() for s in sentences if s.strip()]


def _last_sentence(text: str) -> str:
    """Extract the last sentence from text."""
    sentences = _split_sentences(text)
    return sentences[-1] if sentences else ""


def _merge_short_paragraphs(paragraphs: List[str], min_tokens: int = MIN_TOKENS) -> List[str]:
    """Merge consecutive short paragraphs."""
    if not paragraphs:
        return []
    merged = []
    buffer = paragraphs[0]

    for para in paragraphs[1:]:
        if _count_tokens(buffer) < min_tokens:
            buffer = buffer + "\n\n" + para
        else:
            merged.append(buffer)
            buffer = para

    merged.append(buffer)
    return merged


def _split_long_paragraph(text: str, max_tokens: int = MAX_TOKENS) -> List[str]:
    """Split a long paragraph at sentence boundaries near TARGET_TOKENS."""
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text]  # Can't split a single sentence

    chunks = []
    current = ""

    for sentence in sentences:
        candidate = (current + " " + sentence).strip() if current else sentence
        if _count_tokens(candidate) > max_tokens and current:
            chunks.append(current)
            current = sentence
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks


def chunk_document(
    body: str,
    overlap_sentences: int = 1,
    links: Optional[List[Dict]] = None,
    method: str = "hierarchical",
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
    target_tokens: int = TARGET_TOKENS,
) -> List[PassageChunk]:
    """Chunk article body into passages.

    Args:
        body: Article body text.
        overlap_sentences: Number of sentences to overlap between chunks (default 1).
        links: Optional list of link dicts with 'url' and position info.
        method: Chunking strategy:
            "hierarchical" (default) — section-aware: detects headings, groups paragraphs
                under sections. Each PassageChunk carries section_title + section_index.
            "paragraph" — flat paragraph splitting (legacy, no section awareness).
            "section"   — alias for hierarchical.
            "sentence"  — split at sentence boundaries (for short-form content).

    Returns:
        List of PassageChunk objects.
    """
    if not body or not body.strip():
        return []

    # "paragraph" method: legacy flat chunking without section detection
    if method == "paragraph":
        paragraphs = _split_paragraphs(body)
        if not paragraphs:
            return [PassageChunk(content=body, chunk_index=0)]
        paragraphs = _merge_short_paragraphs(paragraphs, min_tokens=min_tokens)
        expanded: List[str] = []
        for para in paragraphs:
            if _count_tokens(para) > max_tokens:
                expanded.extend(_split_long_paragraph(para, max_tokens=max_tokens))
            else:
                expanded.append(para)
        chunks: List[PassageChunk] = []
        prev = ""
        for i, text in enumerate(expanded):
            overlap = prev if (i > 0 and overlap_sentences > 0) else ""
            chunk = PassageChunk(content=text, overlap_prefix=overlap, chunk_index=i)
            if links:
                lo = text.lower()
                chunk.links_in_chunk = [l for l in links if l.get("url", "").lower() in lo or l.get("anchor", "").lower() in lo]
            chunks.append(chunk)
            prev = _last_sentence(text)
        return chunks

    # "sentence" method: split at sentence boundaries (short-form, social media)
    if method == "sentence":
        sentences = _split_sentences(body)
        chunks = []
        for i, s in enumerate(sentences):
            if s.strip():
                chunks.append(PassageChunk(content=s.strip(), chunk_index=i))
        return chunks

    # "hierarchical" / "section" (default): section-aware chunking
    # 1. Split into paragraphs
    paragraphs = _split_paragraphs(body)
    if not paragraphs:
        return [PassageChunk(content=body, chunk_index=0)]

    # 2. Detect sections (headings group paragraphs hierarchically)
    sections = _split_sections(paragraphs)

    # 3. Within each section: merge short paragraphs, split long ones
    chunks: List[PassageChunk] = []
    global_chunk_index = 0
    prev_last_sentence = ""

    section_counter = 0
    for heading_path, heading_depth, section_paras in sections:
        merged = _merge_short_paragraphs(section_paras, min_tokens=min_tokens)

        expanded: List[str] = []
        for para in merged:
            if _count_tokens(para) > max_tokens:
                expanded.extend(_split_long_paragraph(para, max_tokens=max_tokens))
            else:
                expanded.append(para)

        # 4. Build PassageChunk objects with overlap and section metadata
        section_title = heading_path[-1] if heading_path else ""
        for text in expanded:
            overlap = prev_last_sentence if (global_chunk_index > 0 and overlap_sentences > 0) else ""

            chunk = PassageChunk(
                content=text,
                overlap_prefix=overlap,
                chunk_index=global_chunk_index,
                section_title=section_title,
                section_index=section_counter,
                heading_path=list(heading_path),
                heading_depth=heading_depth,
            )

            # Attach links that appear in this chunk
            if links:
                chunk_lower = text.lower()
                chunk.links_in_chunk = [
                    lnk for lnk in links
                    if lnk.get("url", "").lower() in chunk_lower
                    or lnk.get("anchor", "").lower() in chunk_lower
                ]

            chunks.append(chunk)
            prev_last_sentence = _last_sentence(text)
            global_chunk_index += 1

        section_counter += 1

    return chunks
