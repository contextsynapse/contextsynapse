"""
Pipeline Connector Router
===========================
FastAPI router exposing batch ingestion, streaming control, and webhook
endpoints.  All three transports converge to the same
``PipelineConnector.ingest`` path — domain-agnostic, works with any entity.

Routes:
  POST /api/v1/pipeline/ingest          — single payload
  POST /api/v1/pipeline/ingest-batch    — array of payloads
  GET  /api/v1/pipeline/stats           — ingestion stats
  GET  /api/v1/pipeline/history         — recent ingestion log

  POST /api/v1/stream/kafka/start       — start Kafka consumer
  POST /api/v1/stream/kafka/stop        — stop Kafka consumer
  POST /api/v1/stream/redis/start       — start Redis subscriber
  POST /api/v1/stream/redis/stop        — stop Redis subscriber
  GET  /api/v1/stream/status            — active streams

  POST /api/v1/webhook/register         — register webhook
  POST /api/v1/webhook/{name}           — receive webhook event
  GET  /api/v1/webhook/list             — list registered webhooks
  DELETE /api/v1/webhook/{name}         — unregister webhook
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request

logger = logging.getLogger(__name__)


def create_connector_router(*, graph_registry) -> APIRouter:
    """Create and return the pipeline connector router.

    Lazily initialises the ``PipelineConnector``, ``StreamConnector``,
    and ``WebhookConnector`` on first use so that import-time cost is
    near zero and optional dependencies (kafka-python, redis) are only
    required when actually used.
    """

    router = APIRouter(tags=["pipeline-connector"])

    # -- lazy singletons ---------------------------------------------------

    _state: Dict[str, Any] = {}

    def _pipeline():
        if "pipeline" not in _state:
            from ..connectors.pipeline_connector import PipelineConnector
            _state["pipeline"] = PipelineConnector(registry=graph_registry)
        return _state["pipeline"]

    def _stream():
        if "stream" not in _state:
            from ..connectors.pipeline_connector import StreamConnector
            _state["stream"] = StreamConnector(
                registry=graph_registry,
                pipeline_connector=_pipeline(),
            )
        return _state["stream"]

    def _webhook():
        if "webhook" not in _state:
            from ..connectors.pipeline_connector import WebhookConnector
            _state["webhook"] = WebhookConnector(
                registry=graph_registry,
                pipeline_connector=_pipeline(),
            )
        return _state["webhook"]

    # ======================================================================
    # Batch ingestion
    # ======================================================================

    @router.post("/api/v1/pipeline/ingest")
    async def ingest_single(req: dict = Body(...)):
        """Ingest a single payload (Airflow, Dagster, cron, manual).

        Body: PipelinePayload fields (entity, node_type, data, sensor, ...).
        """
        from ..connectors.pipeline_connector import PipelinePayload
        payload = PipelinePayload.from_dict(req)
        result = _pipeline().ingest(payload)
        if result["status"] == "error":
            raise HTTPException(status_code=422, detail=result)
        return result

    @router.post("/api/v1/pipeline/ingest-batch")
    async def ingest_batch(items: List[dict] = Body(...)):
        """Ingest an array of payloads in one call.

        Body: list of PipelinePayload dicts.
        """
        from ..connectors.pipeline_connector import PipelinePayload
        payloads = [PipelinePayload.from_dict(d) for d in items]
        result = _pipeline().ingest_batch(payloads)
        return result

    # ======================================================================
    # Stats / history
    # ======================================================================

    @router.get("/api/v1/pipeline/stats")
    async def pipeline_stats():
        """Return ingestion statistics (total, by source, by entity, errors)."""
        return _pipeline().get_ingest_stats()

    @router.get("/api/v1/pipeline/history")
    async def pipeline_history(limit: int = 50):
        """Return recent ingestion log entries."""
        return {"history": _pipeline().get_history(limit=limit)}

    # ======================================================================
    # Streaming control
    # ======================================================================

    @router.post("/api/v1/stream/kafka/start")
    async def start_kafka(req: dict = Body(...)):
        """Start a Kafka consumer.

        Body: {topic, servers?, group_id?}
        """
        topic = req.get("topic", "")
        if not topic:
            raise HTTPException(400, "topic is required")
        servers = req.get("servers", req.get("bootstrap_servers", "localhost:9092"))
        group_id = req.get("group_id", "contextsynapse")
        try:
            name = _stream().start_kafka_consumer(
                topic=topic,
                bootstrap_servers=servers,
                group_id=group_id,
            )
            return {"status": "started", "name": name, "topic": topic}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/v1/stream/kafka/stop")
    async def stop_kafka(req: dict = Body(...)):
        """Stop a running Kafka consumer.

        Body: {topic}
        """
        topic = req.get("topic", "")
        name = f"kafka:{topic}" if topic else None
        _stream().stop(name)
        return {"status": "stopped", "name": name or "all-kafka"}

    @router.post("/api/v1/stream/redis/start")
    async def start_redis(req: dict = Body(...)):
        """Start a Redis pub/sub subscriber.

        Body: {channel?} — defaults to "pipeline:*"
        """
        channel = req.get("channel", req.get("channel_pattern", "pipeline:*"))
        try:
            name = _stream().start_redis_consumer(channel_pattern=channel)
            return {"status": "started", "name": name, "channel": channel}
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/v1/stream/redis/stop")
    async def stop_redis(req: dict = Body(...)):
        """Stop a running Redis subscriber.

        Body: {channel?}
        """
        channel = req.get("channel", "")
        name = f"redis:{channel}" if channel else None
        _stream().stop(name)
        return {"status": "stopped", "name": name or "all-redis"}

    @router.get("/api/v1/stream/status")
    async def stream_status():
        """Return status of all active stream consumers."""
        return {"streams": _stream().status()}

    # ======================================================================
    # Webhooks
    # ======================================================================

    @router.post("/api/v1/webhook/register")
    async def register_webhook(req: dict = Body(...)):
        """Register a named webhook.

        Body: {name, secret?, default_entity?, default_node_type?, default_sensor?}
        """
        name = req.get("name", "")
        if not name:
            raise HTTPException(400, "name is required")
        info = _webhook().register_webhook(
            name=name,
            secret=req.get("secret", ""),
            default_entity=req.get("default_entity", req.get("entity", "")),
            default_node_type=req.get("default_node_type", req.get("node_type", "Event")),
            default_sensor=req.get("default_sensor", req.get("sensor", "")),
        )
        return {"status": "registered", **info}

    @router.post("/api/v1/webhook/{name}")
    async def receive_webhook(name: str, request: Request):
        """Receive an incoming webhook event.

        The body is parsed as JSON.  HMAC signature is checked against
        headers if the webhook was registered with a secret.
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")

        # Flatten headers to simple dict
        headers = {k.lower(): v for k, v in request.headers.items()}
        result = _webhook().process_webhook(name=name, headers=headers, body=body)
        if result["status"] == "error":
            status_code = 401 if "HMAC" in str(result.get("errors", "")) else 422
            raise HTTPException(status_code=status_code, detail=result)
        return result

    @router.get("/api/v1/webhook/list")
    async def list_webhooks():
        """List all registered webhooks."""
        return {"webhooks": _webhook().list_webhooks()}

    @router.delete("/api/v1/webhook/{name}")
    async def unregister_webhook(name: str):
        """Unregister a webhook by name."""
        ok = _webhook().unregister_webhook(name)
        if not ok:
            raise HTTPException(404, f"Webhook not found: {name}")
        return {"status": "unregistered", "name": name}

    return router
