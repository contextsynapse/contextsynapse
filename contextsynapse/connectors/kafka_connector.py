"""Kafka Connector — subscribes to topics and streams ConnectorDocuments."""

from __future__ import annotations
import json
import logging
import threading
from typing import Callable, List
from .base import BaseConnector, ConnectorConfig, ConnectorDocument

logger = logging.getLogger(__name__)


class KafkaConnector(BaseConnector):
    """Subscribes to Kafka topics for streaming document ingestion.

    Supports both poll mode (batch consume) and subscribe mode (continuous).
    """

    def __init__(self, config: ConnectorConfig):
        config.connector_type = "kafka"
        super().__init__(config)
        self._broker = config.config.get("broker", "localhost:9092")
        self._topic = config.config.get("topic", "")
        self._group_id = config.config.get("group_id", f"contextcore-{config.connector_id}")
        self._consumer = None
        self._running = False
        self._callback = None

    def _get_consumer(self):
        if self._consumer is None:
            try:
                from kafka import KafkaConsumer
                self._consumer = KafkaConsumer(
                    self._topic,
                    bootstrap_servers=self._broker,
                    group_id=self._group_id,
                    auto_offset_reset="latest",
                    value_deserializer=lambda m: json.loads(m.decode("utf-8")),
                    consumer_timeout_ms=5000,  # 5s timeout for poll mode
                )
            except ImportError:
                logger.warning("kafka-python not installed: pip install kafka-python")
                return None
            except Exception as e:
                logger.error("[KAFKA] Connection failed: %s", e)
                return None
        return self._consumer

    def poll(self) -> List[ConnectorDocument]:
        """Batch consume — reads available messages (up to 5s timeout)."""
        consumer = self._get_consumer()
        if not consumer:
            return []

        docs = []
        try:
            for msg in consumer:
                payload = msg.value if isinstance(msg.value, dict) else {}
                docs.append(ConnectorDocument(
                    title=payload.get("title", ""),
                    content=payload.get("content", payload.get("text", json.dumps(payload))),
                    url=payload.get("url", ""),
                    source=f"kafka:{self._topic}",
                    published_at=payload.get("timestamp", ""),
                    tags=payload.get("tags", []),
                    metadata={"kafka_topic": self._topic, "kafka_offset": msg.offset},
                    doc_type=payload.get("doc_type", "event"),
                ))
                if len(docs) >= 50:  # max 50 per poll
                    break
        except Exception as e:
            logger.error("[KAFKA] Poll error: %s", e)

        return docs

    def subscribe(self, callback: Callable[[ConnectorDocument], None]):
        """Continuous streaming — calls callback for each message."""
        self._callback = callback
        self._running = True
        thread = threading.Thread(target=self._stream_loop, daemon=True, name=f"kafka-{self._topic}")
        thread.start()

    def _stream_loop(self):
        consumer = self._get_consumer()
        if not consumer:
            return
        try:
            # Reset timeout for streaming mode
            consumer.config['consumer_timeout_ms'] = float("inf")
            for msg in consumer:
                if not self._running:
                    break
                payload = msg.value if isinstance(msg.value, dict) else {}
                doc = ConnectorDocument(
                    title=payload.get("title", ""),
                    content=payload.get("content", json.dumps(payload)),
                    url=payload.get("url", ""),
                    source=f"kafka:{self._topic}",
                    doc_type="event",
                )
                if self._callback:
                    self._callback(doc)
        except Exception as e:
            logger.error("[KAFKA] Stream error: %s", e)
        finally:
            self._running = False

    def health_check(self) -> bool:
        try:
            from kafka import KafkaConsumer
            test = KafkaConsumer(bootstrap_servers=self._broker, consumer_timeout_ms=2000)
            test.close()
            return True
        except Exception:
            return False

    def stop(self):
        self._running = False
        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass
            self._consumer = None
