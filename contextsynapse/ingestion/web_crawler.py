"""
Web Crawler — Multi-page website crawling for context ingestion.

Crawls a website starting from a URL, following internal links,
respecting robots.txt, and extracting clean text content.

Usage:
    from contextsynapse.ingestion.web_crawler import crawl_website
    pages = crawl_website("https://example.com", max_pages=20, max_depth=2)
    # [{"url": "...", "title": "...", "content": "...", "links": [...]}]
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = "AIContextDB-Crawler/1.0"
REQUEST_DELAY = 1.0  # seconds between requests (polite crawling)


def crawl_website(
    start_url: str,
    max_pages: int = 20,
    max_depth: int = 2,
    same_domain_only: bool = True,
    user_agent: str = DEFAULT_USER_AGENT,
    on_page: Optional[callable] = None,
) -> List[Dict[str, Any]]:
    """Crawl a website and return extracted pages.

    Args:
        start_url: Starting URL to crawl
        max_pages: Maximum number of pages to fetch
        max_depth: Maximum link-following depth from start
        same_domain_only: Only follow links on the same domain
        user_agent: User-Agent header
        on_page: Callback(page_dict) called after each page is fetched

    Returns:
        List of {url, title, content, links, depth, char_count}
    """
    parsed_start = urlparse(start_url)
    base_domain = parsed_start.netloc

    # Check robots.txt
    robots = _check_robots(f"{parsed_start.scheme}://{base_domain}", user_agent)

    # Try sitemap first for URL discovery
    sitemap_urls = _parse_sitemap(f"{parsed_start.scheme}://{base_domain}")

    # BFS crawl
    visited: Set[str] = set()
    pages: List[Dict[str, Any]] = []
    queue: deque = deque()

    # Seed with start URL + sitemap URLs
    queue.append((start_url, 0))
    for surl in sitemap_urls[:max_pages]:
        if surl != start_url:
            queue.append((surl, 1))

    while queue and len(pages) < max_pages:
        url, depth = queue.popleft()

        # Normalize URL
        url = _normalize_url(url)
        if url in visited:
            continue
        if depth > max_depth:
            continue

        # robots.txt check
        if robots and not robots.can_fetch(user_agent, url):
            logger.debug("[CRAWL] Blocked by robots.txt: %s", url)
            continue

        visited.add(url)

        # Fetch and extract
        try:
            page = _fetch_page(url, user_agent)
            if not page:
                continue

            page["depth"] = depth
            pages.append(page)

            if on_page:
                on_page(page)

            logger.info("[CRAWL] %d/%d fetched: %s (%d chars)",
                        len(pages), max_pages, url[:60], page.get("char_count", 0))

            # Follow internal links
            if depth < max_depth:
                for link in page.get("links", []):
                    abs_link = urljoin(url, link)
                    abs_link = _normalize_url(abs_link)
                    if abs_link in visited:
                        continue
                    if same_domain_only and urlparse(abs_link).netloc != base_domain:
                        continue
                    if _is_valid_page_url(abs_link):
                        queue.append((abs_link, depth + 1))

            # Polite delay
            time.sleep(REQUEST_DELAY)

        except Exception as e:
            logger.debug("[CRAWL] Failed to fetch %s: %s", url[:60], e)

    logger.info("[CRAWL] Complete: %d pages from %s", len(pages), base_domain)
    return pages


def fetch_single_page(url: str, user_agent: str = DEFAULT_USER_AGENT) -> Optional[Dict[str, Any]]:
    """Fetch a single URL and extract clean text. Used by web_fetch tool."""
    return _fetch_page(url, user_agent)


def _fetch_page(url: str, user_agent: str) -> Optional[Dict[str, Any]]:
    """Fetch one page and extract content."""
    import requests

    try:
        resp = requests.get(url, timeout=15, headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml",
        })
        if resp.status_code != 200:
            return None
        if "text/html" not in resp.headers.get("content-type", ""):
            return None
    except Exception as e:
        logger.debug("[CRAWL] Request failed for %s: %s", url[:60], e)
        return None

    html = resp.text
    title = ""
    content = ""
    links = []

    # Always extract links from HTML (BeautifulSoup or regex)
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href and not href.startswith(("#", "javascript:", "mailto:")):
                links.append(href)
    except ImportError:
        # Regex fallback for links
        for match in re.findall(r'href=["\']([^"\'#]+)["\']', html):
            if not match.startswith(("javascript:", "mailto:")):
                links.append(match)
        # Regex title
        title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
    except Exception:
        pass

    # Extract clean article text with trafilatura (best quality)
    try:
        import trafilatura
        content = trafilatura.extract(html, include_links=False, include_tables=True) or ""
        # Get better title from trafilatura metadata
        try:
            meta_json = trafilatura.extract(html, output_format="json")
            if meta_json:
                import json
                meta = json.loads(meta_json) if isinstance(meta_json, str) else meta_json
                title = meta.get("title", "") or title
        except Exception:
            pass
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: BeautifulSoup text extraction if trafilatura failed
    if not content:
        try:
            from bs4 import BeautifulSoup
            soup2 = BeautifulSoup(html, "html.parser")
            for tag in soup2(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            content = soup2.get_text(separator="\n", strip=True)
        except ImportError:
            content = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL | re.IGNORECASE)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"\s+", " ", content).strip()

    if not content or len(content) < 50:
        return None

    return {
        "url": url,
        "title": title or urlparse(url).path.strip("/").split("/")[-1] or "Untitled",
        "content": content,
        "links": links[:50],  # cap link count
        "char_count": len(content),
    }


def _check_robots(base_url: str, user_agent: str) -> Optional[RobotFileParser]:
    """Check robots.txt for the domain."""
    try:
        rp = RobotFileParser()
        rp.set_url(f"{base_url}/robots.txt")
        rp.read()
        return rp
    except Exception:
        return None


def _parse_sitemap(base_url: str) -> List[str]:
    """Try to parse sitemap.xml for URL discovery."""
    urls = []
    try:
        import requests
        resp = requests.get(f"{base_url}/sitemap.xml", timeout=5)
        if resp.status_code == 200 and "xml" in resp.headers.get("content-type", ""):
            # Simple regex extraction of URLs from sitemap
            for match in re.findall(r"<loc>(https?://[^<]+)</loc>", resp.text):
                urls.append(match)
            logger.info("[CRAWL] Found %d URLs in sitemap.xml", len(urls))
    except Exception:
        pass
    return urls


def _normalize_url(url: str) -> str:
    """Normalize a URL (strip fragment, trailing slash)."""
    parsed = urlparse(url)
    # Remove fragment
    clean = parsed._replace(fragment="")
    result = clean.geturl()
    # Strip trailing slash for consistency
    if result.endswith("/") and len(parsed.path) > 1:
        result = result[:-1]
    return result


def _is_valid_page_url(url: str) -> bool:
    """Check if a URL is likely a web page (not an asset)."""
    skip_extensions = {
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico",
        ".css", ".js", ".xml", ".json", ".zip", ".tar", ".gz",
        ".mp3", ".mp4", ".wav", ".avi", ".mov",
        ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    }
    parsed = urlparse(url)
    path = parsed.path.lower()
    return not any(path.endswith(ext) for ext in skip_extensions)
