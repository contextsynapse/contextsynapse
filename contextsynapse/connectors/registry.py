"""ConnectorRegistry — YAML-driven connector auto-instantiation.

Loads connector configurations from YAML files and creates connector
instances using registered connector classes.

Usage:
    reg = ConnectorRegistry()
    reg.register_connector_class("amfi_nav", AmfiNavConnector)
    reg.load_from_directory("config/connectors/")
    connectors = reg.create_all()
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

import yaml

from .base import BaseConnector, ConnectorConfig

logger = logging.getLogger(__name__)


class ConnectorRegistry:
    """Loads YAML configs and instantiates connectors by type."""

    def __init__(self):
        self._classes: Dict[str, Type[BaseConnector]] = {}
        self._configs: List[ConnectorConfig] = []

    @property
    def configs(self) -> List[ConnectorConfig]:
        return list(self._configs)

    def register_connector_class(
        self, connector_type: str, cls: Type[BaseConnector]
    ) -> None:
        """Register a connector class for a given type string."""
        self._classes[connector_type] = cls

    def get_connector_class(self, connector_type: str) -> Type[BaseConnector]:
        """Get the registered class for a connector type."""
        if connector_type not in self._classes:
            raise KeyError(
                f"Unknown connector type: {connector_type!r}. "
                f"Registered: {list(self._classes.keys())}"
            )
        return self._classes[connector_type]

    def load_from_yaml(self, path: str) -> ConnectorConfig:
        """Load and validate a single YAML connector config."""
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        if not data.get("name"):
            raise ValueError(
                f"Connector config {path} missing required field: name"
            )
        if not data.get("connector_type"):
            raise ValueError(
                f"Connector config {path} missing required field: connector_type"
            )

        config = ConnectorConfig(
            connector_id=data.get("connector_id", ""),
            connector_type=data["connector_type"],
            name=data["name"],
            target_context_id=data.get("target_context_id", ""),
            pipeline=data.get("pipeline", "builtin:knowledge-graph"),
            llm_model=data.get("llm_model", ""),
            embedding_model=data.get("embedding_model", ""),
            poll_interval_minutes=data.get("poll_interval_minutes", 5),
            active=data.get("active", True),
            config=data.get("config", {}),
        )
        self._configs.append(config)
        return config

    def load_from_directory(self, config_dir: str) -> int:
        """Load all YAML configs from a directory. Returns count loaded."""
        loaded = 0
        config_path = Path(config_dir)
        if not config_path.is_dir():
            logger.warning("[REGISTRY] Config dir not found: %s", config_dir)
            return 0

        for f in sorted(config_path.iterdir()):
            if f.suffix not in (".yaml", ".yml"):
                continue
            try:
                self.load_from_yaml(str(f))
                loaded += 1
            except Exception as e:
                logger.warning("[REGISTRY] Skipping %s: %s", f.name, e)
        return loaded

    def create_connector(self, config: ConnectorConfig) -> BaseConnector:
        """Create a connector instance from a config."""
        cls = self.get_connector_class(config.connector_type)
        return cls(config)

    def create_all(self) -> List[BaseConnector]:
        """Create connector instances for all loaded configs."""
        connectors = []
        for config in self._configs:
            try:
                connector = self.create_connector(config)
                connectors.append(connector)
            except KeyError:
                logger.warning(
                    "[REGISTRY] No class registered for type %r, skipping %s",
                    config.connector_type, config.name,
                )
        return connectors
