"""
Text Chunking Utility
=====================
Single canonical implementation of text chunking used by all ingestion paths.
"""

from __future__ import annotations

import re
from typing import List


def chunk_text(text: str, max_chars: int = 2000) -> List[str]:
    """Split text into chunks by paragraphs, up to max_chars each.

    Tries double-newline splits first, then single-newline, then
    sentence boundaries as fallback — so trafilatura output (which
    often uses single newlines) gets properly chunked.
    """
    if len(text) <= max_chars:
        return [text]

    # Try double-newline paragraph splits first
    paragraphs = text.split("\n\n")

    # If that produced only 1 block (common with trafilatura), try single newlines
    if len(paragraphs) <= 1:
        paragraphs = text.split("\n")

    # If still only 1 block (no newlines at all), split by sentence boundaries
    if len(paragraphs) <= 1:
        paragraphs = re.split(r'(?<=[.!?])\s+', text)

    chunks, current = [], ""
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if len(current) + len(p) + 2 > max_chars and current:
            chunks.append(current.strip())
            current = p
        else:
            current = current + "\n\n" + p if current else p
    if current.strip():
        chunks.append(current.strip())

    # Final safety: if any chunk is still > max_chars, hard-split it
    result = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
        else:
            for i in range(0, len(chunk), max_chars):
                piece = chunk[i:i + max_chars].strip()
                if piece:
                    result.append(piece)

    return result or [text[:max_chars]]
