"""
Prometheus metrics middleware for AIContextDB.

Exposes counters and histograms that a Prometheus scraper can collect
from the ``/metrics`` endpoint.
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


try:
    from prometheus_client import (
        Counter,
        Histogram,
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False


# ── Metric definitions ───────────────────────────────────────────────

if PROMETHEUS_AVAILABLE:
    REQUEST_COUNT = Counter(
        "contextcore_requests_total",
        "Total HTTP requests",
        ["method", "endpoint", "status"],
    )
    REQUEST_DURATION = Histogram(
        "contextcore_request_duration_seconds",
        "Request latency in seconds",
        ["method", "endpoint"],
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    ACTIVE_REQUESTS = Gauge(
        "contextcore_active_requests",
        "Number of in-flight requests",
    )
    PII_DETECTIONS = Counter(
        "contextcore_pii_detections_total",
        "Total PII detections during ingest",
        ["pii_type"],
    )


# ── Middleware ────────────────────────────────────────────────────────

class PrometheusMiddleware(BaseHTTPMiddleware):
    """Record request count, latency, and in-flight gauge."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not PROMETHEUS_AVAILABLE:
            return await call_next(request)

        # Normalise path (strip trailing slash, collapse IDs for cardinality)
        path = request.url.path.rstrip("/") or "/"

        ACTIVE_REQUESTS.inc()
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            REQUEST_COUNT.labels(
                method=request.method, endpoint=path, status="500"
            ).inc()
            raise
        finally:
            duration = time.perf_counter() - start
            ACTIVE_REQUESTS.dec()

        REQUEST_COUNT.labels(
            method=request.method, endpoint=path, status=str(response.status_code)
        ).inc()
        REQUEST_DURATION.labels(method=request.method, endpoint=path).observe(duration)

        return response


# ── Helpers ───────────────────────────────────────────────────────────

def record_pii_detection(pii_type: str = "unknown") -> None:
    """Increment the PII detection counter (safe to call even without prometheus)."""
    if PROMETHEUS_AVAILABLE:
        PII_DETECTIONS.labels(pii_type=pii_type).inc()


def metrics_response() -> Response:
    """Return a Prometheus-compatible ``/metrics`` response."""
    if not PROMETHEUS_AVAILABLE:
        return Response(
            content="# prometheus_client not installed\n",
            media_type="text/plain",
        )
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def mount_metrics(app: FastAPI) -> None:
    """Add the middleware and ``/metrics`` endpoint to *app*."""
    app.add_middleware(PrometheusMiddleware)

    @app.get("/metrics", include_in_schema=False)
    async def _metrics():
        return metrics_response()
