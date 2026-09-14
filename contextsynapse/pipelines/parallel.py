"""Parallel Pipeline Executor — run multiple sensors concurrently.

Instead of running sensors sequentially (5s each = 30s total),
run them in parallel with a ThreadPool (5s each = 5s total).

Usage:
    from contextsynapse.pipelines.parallel import run_parallel

    results = run_parallel([
        ("price_feed", price_fn, {"ticker": "TCS.NS"}),
        ("news_rss", news_fn, {"sources": [...]}),
        ("technical", tech_fn, {"symbol": "TCS"}),
    ], max_workers=4, timeout=30)
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError, as_completed
from typing import Any, Callable

logger = logging.getLogger(__name__)


def run_parallel(
    tasks: list[tuple[str, Callable, dict]],
    max_workers: int = 4,
    timeout: int = 30,
) -> dict[str, Any]:
    """Run tasks in parallel. Returns {name: result} dict.

    Args:
        tasks: [(name, fn, kwargs), ...]
        max_workers: thread pool size
        timeout: max seconds per task
    """
    results = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for name, fn, kwargs in tasks:
            future = pool.submit(fn, **kwargs)
            futures[future] = name

        for future in as_completed(futures, timeout=timeout + 5):
            name = futures[future]
            try:
                results[name] = future.result(timeout=timeout)
            except TimeoutError:
                errors[name] = "timeout"
                logger.warning("[PARALLEL] %s timed out", name)
            except Exception as e:
                errors[name] = str(e)
                logger.warning("[PARALLEL] %s failed: %s", name, e)

    return {"results": results, "errors": errors, "total": len(tasks),
            "completed": len(results), "failed": len(errors)}
