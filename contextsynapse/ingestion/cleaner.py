"""Web content cleaner using Trafilatura.

Extracts clean article text, metadata, and links from HTML or URLs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class ExtractedLink:
    """A hyperlink found in article content."""
    url: str
    anchor: str = ""
    context: str = ""
    domain: str = ""
    link_type: str = "external"  # internal | external

    def __post_init__(self):
        if self.url and not self.domain:
            try:
                self.domain = urlparse(self.url).netloc
            except Exception:
                pass


@dataclass
class CleanDocument:
    """Cleaned article content ready for chunking."""
    title: str
    author: str = ""
    date: str = ""
    body: str = ""
    links: List[ExtractedLink] = field(default_factory=list)
    source_url: str = ""
    source_name: str = ""
    word_count: int = 0
    language: str = ""

    def __post_init__(self):
        if self.body and not self.word_count:
            self.word_count = len(self.body.split())
        if self.source_url and not self.source_name:
            try:
                self.source_name = urlparse(self.source_url).netloc
            except Exception:
                pass


def _extract_links_from_text(text: str, source_domain: str = "") -> List[ExtractedLink]:
    """Extract URLs from text — handles raw URLs and markdown [anchor](url) format."""
    seen: set = set()
    links: List[ExtractedLink] = []

    def _add(url: str, anchor: str = "", context: str = ""):
        url = url.rstrip(".,;:)")
        if not url or url in seen:
            return
        seen.add(url)
        domain = ""
        link_type = "external"
        try:
            domain = urlparse(url).netloc
            if source_domain and domain == source_domain:
                link_type = "internal"
        except Exception:
            pass
        links.append(ExtractedLink(url=url, anchor=anchor, context=context,
                                   domain=domain, link_type=link_type))

    # Markdown links: [anchor text](url)
    md_pattern = re.compile(r'\[([^\]]*)\]\((https?://[^\)]+)\)')
    for m in md_pattern.finditer(text):
        anchor = m.group(1).strip()
        url = m.group(2).strip()
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 40)
        _add(url, anchor=anchor, context=text[start:end].strip())

    # Bare URLs not already captured
    bare_pattern = re.compile(r'https?://[^\s<>"{}|\\^`\[\]()]+')
    for m in bare_pattern.finditer(text):
        url = m.group().rstrip(".,;:)")
        if url not in seen:
            start = max(0, m.start() - 50)
            end = min(len(text), m.end() + 50)
            _add(url, context=text[start:end].strip())

    return links


def clean_webpage(url_or_html: str, source_url: str = "") -> CleanDocument:
    """Clean a web page into structured article content.

    Args:
        url_or_html: URL to fetch, or raw HTML string.
        source_url: Original URL (if url_or_html is HTML, provide this for link resolution).

    Returns:
        CleanDocument with clean body text, metadata, and extracted links.
    """
    try:
        import trafilatura
    except ImportError:
        logger.error("trafilatura not installed: pip install trafilatura")
        return CleanDocument(title="", body=url_or_html if "<" not in url_or_html else "")

    is_url = url_or_html.startswith("http://") or url_or_html.startswith("https://")
    actual_url = url_or_html if is_url else source_url

    # Fetch if URL
    if is_url:
        downloaded = trafilatura.fetch_url(url_or_html)
        if not downloaded:
            logger.warning("Failed to fetch URL: %s", url_or_html)
            return CleanDocument(title="", source_url=url_or_html)
        html = downloaded
    else:
        html = url_or_html

    # Extract with metadata
    result = trafilatura.extract(
        html,
        include_links=True,
        include_tables=True,
        output_format="markdown",
        with_metadata=False,
    )

    # Also get metadata separately
    metadata = trafilatura.extract(
        html,
        output_format="json",
        with_metadata=True,
    )

    title = ""
    author = ""
    date = ""

    if metadata:
        import json
        try:
            meta = json.loads(metadata) if isinstance(metadata, str) else metadata
            title = meta.get("title", "")
            author = meta.get("author", "")
            date = meta.get("date", "")
        except (json.JSONDecodeError, TypeError):
            pass

    body = result or ""

    # Extract links
    source_domain = ""
    if actual_url:
        try:
            source_domain = urlparse(actual_url).netloc
        except Exception:
            pass
    links = _extract_links_from_text(body, source_domain)

    return CleanDocument(
        title=title or "",
        author=author or "",
        date=date or "",
        body=body,
        links=links,
        source_url=actual_url,
        source_name=source_domain,
    )


def clean_text(text: str, title: str = "", source_url: str = "") -> CleanDocument:
    """Clean raw text (not HTML) into a CleanDocument.

    Use this when content is already extracted (e.g., from RSS feed body, API response).
    """
    links = _extract_links_from_text(text)
    return CleanDocument(
        title=title,
        body=text,
        links=links,
        source_url=source_url,
    )
