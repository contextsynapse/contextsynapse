"""LLM enrichment — deep code summaries for CodeModule nodes."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from .helpers import _safe_read

logger = logging.getLogger(__name__)

_MAX_LLM_FILES = 15

_LLM_PROMPT = """\
Summarize this source code module in 2-3 sentences:
1. What it does (purpose)
2. Key dependencies it imports
3. What could break (risks)

File: {path}

```
{code}
```

Reply with ONLY the summary, no markdown headings."""


def get_llm_client():
    """Import and return the LLM client. Raises if unavailable."""
    from contextsynapse.llm.client import get_llm_client as _get
    return _get()


def scan_with_llm(repo_path: Path, code_nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Enrich CodeModule nodes with LLM-generated summaries.

    Returns the same nodes with richer ``summary`` properties.
    If LLM is unavailable, returns nodes unchanged.
    """
    try:
        client = get_llm_client()
    except Exception:
        logger.debug("scan_with_llm: no LLM available — returning nodes unchanged")
        return code_nodes

    enriched = []
    llm_count = 0

    for node in code_nodes:
        if llm_count >= _MAX_LLM_FILES:
            enriched.append(node)
            continue

        fpath = repo_path / node["properties"].get("path", "")
        if not fpath.is_file():
            enriched.append(node)
            continue

        code = _safe_read(fpath, limit=3000)
        if not code or len(code) < 10:
            enriched.append(node)
            continue

        try:
            prompt = _LLM_PROMPT.format(path=node["properties"].get("path", ""), code=code)
            summary = client.generate(prompt, system="You are a senior code reviewer. Be concise.", max_tokens=200)
            llm_count += 1

            if summary and len(summary.strip()) > 10:
                new_node = dict(node)
                new_node["properties"] = dict(node["properties"])
                new_node["properties"]["summary"] = summary.strip()[:400]
                enriched.append(new_node)
            else:
                enriched.append(node)
        except Exception as exc:
            logger.debug("scan_with_llm: failed for %s — %s", fpath, exc)
            enriched.append(node)

    logger.info("scan_with_llm: enriched %d / %d modules", llm_count, len(code_nodes))
    return enriched
