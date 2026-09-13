"""Pipeline Connector — universal ingestion endpoint for external systems.

Receives data from ANY external pipeline (Airflow, Dagster, Kafka, webhooks)
and routes it into the context graph with proper sensor wiring.

Three transport modes:
  BATCH:     HTTP POST -> process -> respond (Airflow, Dagster, cron)
  STREAMING: Kafka/Redis consumer -> continuous processing
  WEBHOOK:   Event-driven push -> process -> ack

All three converge to the same internal pipeline:
  Parse payload -> Resolve entity -> Create typed nodes -> Wire sensor -> Fuse -> Notify
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standard payload — all transports produce this
# ---------------------------------------------------------------------------

@dataclass
class PipelinePayload:
    """Universal payload format for pipeline ingestion.

    Every transport (batch HTTP, Kafka message, webhook event) normalizes
    its input into this structure before calling ``PipelineConnector.ingest``.
    """

    # Source identification
    source: str = ""              # "airflow", "kafka", "webhook", "dagster", "manual"
    dag_id: str = ""              # Airflow DAG ID
    task_id: str = ""             # task within DAG
    run_id: str = ""              # execution run ID

    # Routing
    entity: str = ""              # target entity ("tcs", "reliance", "project-alpha")
    node_type: str = "Fact"       # graph node type ("Earnings", "Regulation", "Fact")
    sensor: str = ""              # target sensor ("fundamentals", "company_news")

    # Data
    data: dict = field(default_factory=dict)

    # Metadata
    timestamp: str = ""
    confidence: float = 1.0
    sentiment: str = ""           # "positive", "negative", "neutral", ""
    source_url: str = ""
    tags: list = field(default_factory=list)

    # ----- helpers ---------------------------------------------------------

    @property
    def content_hash(self) -> str:
        """Deterministic dedup key for this payload."""
        raw = json.dumps(self.data, sort_keys=True, default=str) + self.entity + self.node_type
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def validate(self) -> List[str]:
        """Return list of validation errors (empty = valid)."""
        errors: List[str] = []
        if not self.entity:
            errors.append("entity is required")
        if not self.data:
            errors.append("data is required")
        if self.confidence < 0 or self.confidence > 1:
            errors.append("confidence must be between 0 and 1")
        if self.sentiment and self.sentiment not in ("positive", "negative", "neutral"):
            errors.append(f"invalid sentiment: {self.sentiment}")
        return errors

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "dag_id": self.dag_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "entity": self.entity,
            "node_type": self.node_type,
            "sensor": self.sensor,
            "data": self.data,
            "timestamp": self.timestamp,
            "confidence": self.confidence,
            "sentiment": self.sentiment,
            "source_url": self.source_url,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PipelinePayload":
        return cls(
            source=d.get("source", ""),
            dag_id=d.get("dag_id", ""),
            task_id=d.get("task_id", ""),
            run_id=d.get("run_id", ""),
            entity=d.get("entity", ""),
            node_type=d.get("node_type", "Fact"),
            sensor=d.get("sensor", ""),
            data=d.get("data", {}),
            timestamp=d.get("timestamp", ""),
            confidence=float(d.get("confidence", 1.0)),
            sentiment=d.get("sentiment", ""),
            source_url=d.get("source_url", ""),
            tags=d.get("tags", []),
        )


# ---------------------------------------------------------------------------
# PipelineConnector — core ingest logic (domain-agnostic)
# ---------------------------------------------------------------------------

class PipelineConnector:
    """Routes payloads from any transport into the context graph.

    This is the single funnel — Kafka, webhooks, Airflow batch calls all
    converge here.  The connector is domain-agnostic: it creates typed
    graph nodes and wires optional sensor readings without knowing what
    domain the entity belongs to.
    """

    def __init__(self, registry):
        """
        Args:
            registry: ``GraphRegistry`` instance for resolving/creating graphs.
        """
        self._registry = registry

        # Stats
        self._stats_lock = threading.Lock()
        self._total_ingested: int = 0
        self._by_source: Dict[str, int] = {}
        self._by_entity: Dict[str, int] = {}
        self._errors: int = 0
        self._history: List[dict] = []          # recent log (bounded)
        self._history_max: int = 500

    # ------------------------------------------------------------------
    # Core ingest
    # ------------------------------------------------------------------

    def ingest(self, payload: PipelinePayload) -> dict:
        """Ingest a single payload into the context graph.

        Steps:
          1. Validate payload
          2. Resolve entity (normalize name, ensure graph exists)
          3. Create typed graph node
          4. If sensor specified, create sensor reading node + edge
          5. Invalidate fusion cache (if applicable)
          6. Return result dict

        Returns:
            {status, node_id, entity, sensor_wired}
        """
        # 1. Validate
        errors = payload.validate()
        if errors:
            self._record_error(payload, "; ".join(errors))
            return {"status": "error", "errors": errors}

        try:
            # 2. Resolve entity
            entity_key = self._normalize_entity(payload.entity)
            db = self._resolve_graph(entity_key)
            if db is None:
                self._record_error(payload, f"Could not resolve graph for entity: {entity_key}")
                return {"status": "error", "errors": [f"graph not found: {entity_key}"]}

            now = payload.timestamp or datetime.now(timezone.utc).isoformat()

            # 3. Create typed node
            node_id = self._create_node(db, payload, entity_key, now)

            # 4. Wire sensor reading (if specified)
            sensor_wired = False
            if payload.sensor:
                sensor_wired = self._wire_sensor(db, payload, entity_key, node_id, now)

            # 5. Invalidate fusion cache
            self._invalidate_fusion(entity_key)

            # 6. Record stats
            self._record_success(payload, node_id)

            return {
                "status": "ok",
                "node_id": node_id,
                "entity": entity_key,
                "node_type": payload.node_type,
                "sensor_wired": sensor_wired,
            }

        except Exception as exc:
            logger.exception("[PIPELINE-CONNECTOR] Ingest failed for entity=%s", payload.entity)
            self._record_error(payload, str(exc))
            return {"status": "error", "errors": [str(exc)]}

    def ingest_batch(self, payloads: List[PipelinePayload]) -> dict:
        """Ingest multiple payloads.  Returns aggregate result."""
        results = []
        ok = 0
        failed = 0
        for p in payloads:
            r = self.ingest(p)
            results.append(r)
            if r["status"] == "ok":
                ok += 1
            else:
                failed += 1
        return {
            "status": "ok" if failed == 0 else "partial",
            "total": len(payloads),
            "ingested": ok,
            "failed": failed,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Stats / history
    # ------------------------------------------------------------------

    def get_ingest_stats(self) -> dict:
        with self._stats_lock:
            return {
                "total_ingested": self._total_ingested,
                "errors": self._errors,
                "by_source": dict(self._by_source),
                "by_entity": dict(self._by_entity),
            }

    def get_history(self, limit: int = 50) -> List[dict]:
        with self._stats_lock:
            return list(reversed(self._history[-limit:]))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_entity(raw: str) -> str:
        """Lowercase, strip whitespace, collapse spaces to underscores."""
        return raw.strip().lower().replace(" ", "_")

    def _resolve_graph(self, entity_key: str):
        """Get or create a graph namespace for the entity."""
        db = self._registry.get_graph(entity_key, load_if_missing=True)
        if db is None:
            # Auto-create a graph for this entity
            try:
                db = self._registry.create_graph(entity_key)
            except Exception:
                return None
        return db

    def _create_node(self, db, payload: PipelinePayload, entity_key: str, now: str) -> str:
        """Create a typed graph node from the payload."""
        from ..core.graph_structures import GraphNode

        node_id = str(uuid.uuid4())

        # Build properties from data + metadata
        props = dict(payload.data)
        props.update({
            "name": payload.data.get("name", payload.data.get("title", f"{payload.node_type} {now[:10]}")),
            "_created_at": now,
            "_source": payload.source,
            "_dag_id": payload.dag_id,
            "_task_id": payload.task_id,
            "_run_id": payload.run_id,
            "_entity": entity_key,
            "_content_hash": payload.content_hash,
        })
        if payload.confidence != 1.0:
            props["confidence"] = payload.confidence
        if payload.sentiment:
            props["sentiment"] = payload.sentiment
        if payload.source_url:
            props["source_url"] = payload.source_url
        if payload.tags:
            props["tags"] = payload.tags

        db.add_node(
            GraphNode(id=node_id, label=payload.node_type, properties=props),
            write_through=True,
        )

        logger.info(
            "[PIPELINE-CONNECTOR] Created %s node %s for entity=%s source=%s",
            payload.node_type, node_id[:8], entity_key, payload.source,
        )
        return node_id

    def _wire_sensor(self, db, payload: PipelinePayload, entity_key: str,
                     source_node_id: str, now: str) -> bool:
        """Create a SensorReading node and wire it to the source node."""
        from ..core.graph_structures import GraphNode, GraphEdge

        try:
            reading_id = str(uuid.uuid4())
            reading_props = {
                "name": f"{payload.sensor} reading",
                "sensor": payload.sensor,
                "entity": entity_key,
                "value": payload.confidence,
                "sentiment": payload.sentiment or "neutral",
                "source": payload.source,
                "_created_at": now,
            }

            db.add_node(
                GraphNode(id=reading_id, label="SensorReading", properties=reading_props),
                write_through=True,
            )

            # Edge: source node --SENSOR_INPUT--> reading
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()),
                source=source_node_id,
                target=reading_id,
                label="SENSOR_INPUT",
                properties={"sensor": payload.sensor, "_created_at": now},
            ))

            logger.debug("[PIPELINE-CONNECTOR] Wired sensor=%s for entity=%s", payload.sensor, entity_key)
            return True

        except Exception as exc:
            logger.warning("[PIPELINE-CONNECTOR] Sensor wiring failed: %s", exc)
            return False

    def _invalidate_fusion(self, entity_key: str):
        """Invalidate any cached fusion score for this entity."""
        try:
            db = self._registry.get_graph(entity_key, load_if_missing=False)
            if db and hasattr(db, "_fusion_cache"):
                db._fusion_cache.clear()
        except Exception:
            pass  # fusion cache is optional

    def _record_success(self, payload: PipelinePayload, node_id: str):
        with self._stats_lock:
            self._total_ingested += 1
            src = payload.source or "unknown"
            self._by_source[src] = self._by_source.get(src, 0) + 1
            ent = self._normalize_entity(payload.entity)
            self._by_entity[ent] = self._by_entity.get(ent, 0) + 1
            self._history.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "ok",
                "entity": ent,
                "node_type": payload.node_type,
                "source": payload.source,
                "node_id": node_id,
            })
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]

    def _record_error(self, payload: PipelinePayload, error: str):
        with self._stats_lock:
            self._errors += 1
            self._history.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "entity": payload.entity,
                "source": payload.source,
                "error": error[:300],
            })
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max:]


# ---------------------------------------------------------------------------
# StreamConnector — Kafka and Redis consumers
# ---------------------------------------------------------------------------

class StreamConnector:
    """Consumes messages from Kafka topics or Redis pub/sub channels and
    routes each message through ``PipelineConnector.ingest``."""

    def __init__(self, registry, pipeline_connector: PipelineConnector):
        self._registry = registry
        self._pipeline = pipeline_connector
        self._consumers: Dict[str, dict] = {}   # name -> {type, thread, stop_event, ...}
        self._lock = threading.Lock()

    # -- Kafka --------------------------------------------------------------

    def start_kafka_consumer(
        self,
        topic: str,
        bootstrap_servers: str = "localhost:9092",
        group_id: str = "contextsynapse",
    ) -> str:
        """Start a background thread consuming from a Kafka topic.

        Each message value is expected to be JSON-serializable to
        ``PipelinePayload`` (or a dict matching its fields).

        Returns:
            Consumer name (used to stop it later).
        """
        name = f"kafka:{topic}"
        if name in self._consumers:
            raise ValueError(f"Consumer already running: {name}")

        stop_event = threading.Event()

        def _consume():
            try:
                from kafka import KafkaConsumer as _KC  # type: ignore
            except ImportError:
                logger.error("[STREAM] kafka-python not installed — cannot consume from Kafka")
                return

            consumer = None
            try:
                consumer = _KC(
                    topic,
                    bootstrap_servers=bootstrap_servers,
                    group_id=group_id,
                    auto_offset_reset="latest",
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    consumer_timeout_ms=2000,
                )
                logger.info("[STREAM] Kafka consumer started: topic=%s servers=%s group=%s",
                            topic, bootstrap_servers, group_id)

                while not stop_event.is_set():
                    try:
                        batch = consumer.poll(timeout_ms=1000, max_records=100)
                        for _tp, messages in batch.items():
                            for msg in messages:
                                try:
                                    payload = PipelinePayload.from_dict(msg.value)
                                    if not payload.source:
                                        payload.source = "kafka"
                                    self._pipeline.ingest(payload)
                                except Exception as exc:
                                    logger.warning("[STREAM] Kafka message parse/ingest error: %s", exc)
                    except Exception as exc:
                        if not stop_event.is_set():
                            logger.warning("[STREAM] Kafka poll error: %s", exc)
                            time.sleep(1)

            except Exception as exc:
                logger.error("[STREAM] Kafka consumer failed: %s", exc)
            finally:
                if consumer:
                    try:
                        consumer.close()
                    except Exception:
                        pass
                logger.info("[STREAM] Kafka consumer stopped: topic=%s", topic)

        thread = threading.Thread(target=_consume, name=f"kafka-{topic}", daemon=True)
        thread.start()

        with self._lock:
            self._consumers[name] = {
                "type": "kafka",
                "topic": topic,
                "servers": bootstrap_servers,
                "group_id": group_id,
                "thread": thread,
                "stop_event": stop_event,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

        return name

    # -- Redis pub/sub ------------------------------------------------------

    def start_redis_consumer(self, channel_pattern: str = "pipeline:*") -> str:
        """Subscribe to Redis pub/sub channels matching *channel_pattern*.

        Each published message is expected to be a JSON string matching
        ``PipelinePayload`` fields.

        Returns:
            Consumer name.
        """
        name = f"redis:{channel_pattern}"
        if name in self._consumers:
            raise ValueError(f"Consumer already running: {name}")

        stop_event = threading.Event()

        def _consume():
            try:
                import redis as _redis  # type: ignore
            except ImportError:
                logger.error("[STREAM] redis package not installed — cannot subscribe")
                return

            import os
            redis_url = os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
            client = None
            pubsub = None

            try:
                client = _redis.from_url(redis_url)
                pubsub = client.pubsub()
                pubsub.psubscribe(channel_pattern)
                logger.info("[STREAM] Redis subscriber started: pattern=%s", channel_pattern)

                while not stop_event.is_set():
                    try:
                        msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                        if msg and msg["type"] in ("message", "pmessage"):
                            try:
                                data = json.loads(msg["data"])
                                payload = PipelinePayload.from_dict(data)
                                if not payload.source:
                                    payload.source = "redis"
                                self._pipeline.ingest(payload)
                            except Exception as exc:
                                logger.warning("[STREAM] Redis message parse/ingest error: %s", exc)
                    except Exception as exc:
                        if not stop_event.is_set():
                            logger.warning("[STREAM] Redis listener error: %s", exc)
                            time.sleep(1)

            except Exception as exc:
                logger.error("[STREAM] Redis subscriber failed: %s", exc)
            finally:
                if pubsub:
                    try:
                        pubsub.unsubscribe()
                        pubsub.close()
                    except Exception:
                        pass
                logger.info("[STREAM] Redis subscriber stopped: pattern=%s", channel_pattern)

        thread = threading.Thread(target=_consume, name=f"redis-{channel_pattern}", daemon=True)
        thread.start()

        with self._lock:
            self._consumers[name] = {
                "type": "redis",
                "channel": channel_pattern,
                "thread": thread,
                "stop_event": stop_event,
                "started_at": datetime.now(timezone.utc).isoformat(),
            }

        return name

    # -- Control ------------------------------------------------------------

    def stop(self, name: Optional[str] = None):
        """Stop a specific consumer or all consumers if *name* is None."""
        with self._lock:
            targets = [name] if name else list(self._consumers.keys())

        for n in targets:
            info = self._consumers.get(n)
            if not info:
                continue
            info["stop_event"].set()
            info["thread"].join(timeout=5)
            with self._lock:
                self._consumers.pop(n, None)
            logger.info("[STREAM] Stopped consumer: %s", n)

    def status(self) -> List[dict]:
        """Return status of all active consumers."""
        with self._lock:
            return [
                {
                    "name": name,
                    "type": info["type"],
                    "alive": info["thread"].is_alive(),
                    "started_at": info.get("started_at", ""),
                    **({"topic": info["topic"], "servers": info["servers"],
                         "group_id": info["group_id"]} if info["type"] == "kafka" else {}),
                    **({"channel": info["channel"]} if info["type"] == "redis" else {}),
                }
                for name, info in self._consumers.items()
            ]


# ---------------------------------------------------------------------------
# WebhookConnector — named webhooks with HMAC verification
# ---------------------------------------------------------------------------

class WebhookConnector:
    """Manages named webhook endpoints.

    Each registered webhook has an optional HMAC secret and an
    entity_resolver callback that extracts routing info from the raw body.
    """

    def __init__(self, registry, pipeline_connector: PipelineConnector):
        self._registry = registry
        self._pipeline = pipeline_connector
        self._webhooks: Dict[str, dict] = {}

    def register_webhook(
        self,
        name: str,
        secret: str = "",
        entity_resolver: Optional[Callable[[dict], dict]] = None,
        default_entity: str = "",
        default_node_type: str = "Event",
        default_sensor: str = "",
    ) -> dict:
        """Register a named webhook.

        Args:
            name: Unique webhook name (used in URL path).
            secret: HMAC-SHA256 secret for signature verification.
                    If empty, signature check is skipped.
            entity_resolver: Optional callable(body_dict) -> dict with keys
                             {entity, node_type, sensor, data}.  If not
                             provided, the body itself is used as data and
                             ``default_*`` values are used for routing.
            default_entity: Fallback entity when resolver is not set.
            default_node_type: Fallback node type.
            default_sensor: Fallback sensor.

        Returns:
            Registration info dict.
        """
        self._webhooks[name] = {
            "name": name,
            "secret": secret,
            "entity_resolver": entity_resolver,
            "default_entity": default_entity,
            "default_node_type": default_node_type,
            "default_sensor": default_sensor,
            "registered_at": datetime.now(timezone.utc).isoformat(),
            "invocations": 0,
            "errors": 0,
        }
        logger.info("[WEBHOOK] Registered webhook: %s", name)
        return {"name": name, "registered_at": self._webhooks[name]["registered_at"]}

    def unregister_webhook(self, name: str) -> bool:
        return self._webhooks.pop(name, None) is not None

    def list_webhooks(self) -> List[dict]:
        return [
            {
                "name": w["name"],
                "has_secret": bool(w["secret"]),
                "default_entity": w["default_entity"],
                "default_node_type": w["default_node_type"],
                "default_sensor": w["default_sensor"],
                "registered_at": w["registered_at"],
                "invocations": w["invocations"],
                "errors": w["errors"],
            }
            for w in self._webhooks.values()
        ]

    def process_webhook(self, name: str, headers: dict, body: dict) -> dict:
        """Validate and process an incoming webhook event.

        Args:
            name: Registered webhook name.
            headers: HTTP headers (used for HMAC verification).
            body: Parsed JSON body.

        Returns:
            Ingest result dict.
        """
        wh = self._webhooks.get(name)
        if not wh:
            return {"status": "error", "errors": [f"unknown webhook: {name}"]}

        # HMAC verification
        if wh["secret"]:
            sig_header = (
                headers.get("x-hub-signature-256", "")
                or headers.get("x-signature-256", "")
                or headers.get("x-webhook-signature", "")
            )
            if not self._verify_hmac(wh["secret"], body, sig_header):
                wh["errors"] += 1
                return {"status": "error", "errors": ["HMAC signature verification failed"]}

        try:
            # Resolve routing via custom resolver or defaults
            resolver = wh.get("entity_resolver")
            if resolver:
                resolved = resolver(body)
                entity = resolved.get("entity", wh["default_entity"])
                node_type = resolved.get("node_type", wh["default_node_type"])
                sensor = resolved.get("sensor", wh["default_sensor"])
                data = resolved.get("data", body)
            else:
                entity = body.get("entity", wh["default_entity"])
                node_type = body.get("node_type", wh["default_node_type"])
                sensor = body.get("sensor", wh["default_sensor"])
                data = body

            if not entity:
                wh["errors"] += 1
                return {"status": "error", "errors": ["could not resolve entity from webhook body"]}

            payload = PipelinePayload(
                source=f"webhook:{name}",
                entity=entity,
                node_type=node_type,
                sensor=sensor,
                data=data,
                timestamp=datetime.now(timezone.utc).isoformat(),
                confidence=float(body.get("confidence", 1.0)),
                sentiment=body.get("sentiment", ""),
                source_url=body.get("source_url", ""),
                tags=body.get("tags", []),
            )

            result = self._pipeline.ingest(payload)
            wh["invocations"] += 1
            return result

        except Exception as exc:
            wh["errors"] += 1
            logger.exception("[WEBHOOK] Processing failed for webhook=%s", name)
            return {"status": "error", "errors": [str(exc)]}

    # -- HMAC ---------------------------------------------------------------

    @staticmethod
    def _verify_hmac(secret: str, body: dict, signature: str) -> bool:
        """Verify HMAC-SHA256 signature (GitHub-style ``sha256=...``)."""
        if not signature:
            return False
        raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        # Support both bare hex and "sha256=hex" format
        sig_value = signature.removeprefix("sha256=")
        return hmac.compare_digest(expected, sig_value)
