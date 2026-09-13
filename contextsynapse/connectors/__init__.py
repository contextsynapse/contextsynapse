"""Connectors — integrations with external data sources."""
from .github import GitHubConnector
from .jira import JiraConnector
from .base import BaseConnector, ConnectorManager, ConnectorConfig, ConnectorDocument, ConnectorStatus
from .kafka_connector import KafkaConnector
from .registry import ConnectorRegistry
from .pipeline_connector import PipelinePayload, PipelineConnector, StreamConnector, WebhookConnector

__all__ = [
    "GitHubConnector", "JiraConnector",
    "BaseConnector", "ConnectorManager", "ConnectorConfig", "ConnectorDocument", "ConnectorStatus",
    "KafkaConnector",
    "ConnectorRegistry",
    "PipelinePayload", "PipelineConnector", "StreamConnector", "WebhookConnector",
]
