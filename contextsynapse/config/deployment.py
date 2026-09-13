"""
Deployment Configuration — single settings file for local, Docker, and K8s.

All deployment-specific behavior is controlled here. Switch between local dev,
Docker Compose, and Kubernetes by changing CONTEXTSYNAPSE_DEPLOY_MODE env var
or editing deploy_settings.yaml.

Usage:
    from contextsynapse.config.deployment import get_deploy_config

    config = get_deploy_config()
    storage_backend = config.storage_backend  # "local", "s3", "gcs"
    redis_url = config.redis_url
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Deployment modes
MODE_LOCAL = "local"         # Local dev — file storage, optional Redis
MODE_DOCKER = "docker"       # Docker Compose — Redis + local volumes
MODE_K8S = "kubernetes"      # Kubernetes — Redis + shared/cloud storage


@dataclass
class DeployConfig:
    """Deployment configuration — all infra settings in one place."""

    # Core
    mode: str = MODE_LOCAL
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    workers: int = 1
    log_level: str = "info"

    # Redis
    redis_url: str = ""
    redis_enabled: bool = False

    # Storage backend: "local", "s3", "gcs", "azure"
    storage_backend: str = "local"
    storage_path: str = "contextcore_data"  # local path or bucket prefix

    # S3 settings (when storage_backend = "s3")
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_prefix: str = "graphs/"
    s3_endpoint: str = ""  # for MinIO or R2

    # GCS settings (when storage_backend = "gcs")
    gcs_bucket: str = ""
    gcs_prefix: str = "graphs/"

    # Azure Blob settings (when storage_backend = "azure")
    azure_container: str = ""
    azure_connection_string: str = ""

    # Graph backend: "redis" (shared, default) or "csr" (in-memory, dev only)
    graph_backend: str = "redis"

    # Vector DB
    vector_backend: str = "custom"  # "custom", "qdrant", "chromadb"
    qdrant_url: str = ""

    # Embedding
    embedding_provider: str = "ollama"  # "ollama", "openai", "cohere"
    embedding_model: str = "nomic-embed-text"
    embedding_url: str = "http://localhost:11434"

    # LLM
    llm_provider: str = "groq"
    llm_model: str = "gpt-oss-120b"
    llm_max_concurrent: int = 10
    llm_rpm: int = 100

    # Ingestion workers
    ingestion_workers: int = 2
    ingestion_queue_max: int = 100

    # Quotas (SaaS)
    default_graphs_per_tenant: int = 50
    default_nodes_per_tenant: int = 100000
    default_storage_mb_per_tenant: int = 500

    # K8s specific
    k8s_namespace: str = "contextsynapse"
    k8s_service_account: str = "contextcore-sa"
    pod_memory_limit: str = "2Gi"
    pod_cpu_limit: str = "1"

    # Write-behind
    write_behind_interval: int = 10
    max_graphs_in_memory: int = 50

    def to_env(self) -> Dict[str, str]:
        """Export config as environment variables (uses new CONTEXTSYNAPSE_ prefix)."""
        env = {}
        if self.redis_url:
            env["CONTEXTSYNAPSE_REDIS_URL"] = self.redis_url
        env["CONTEXTSYNAPSE_GRAPH_BACKEND"] = self.graph_backend
        if self.qdrant_url:
            env["QDRANT_URL"] = self.qdrant_url
        env["CONTEXTSYNAPSE_DEPLOY_MODE"] = self.mode
        env["CONTEXTSYNAPSE_STORAGE_BACKEND"] = self.storage_backend
        env["CONTEXTSYNAPSE_STORAGE_PATH"] = self.storage_path
        env["CONTEXTSYNAPSE_LLM_MAX_CONCURRENT"] = str(self.llm_max_concurrent)
        env["CONTEXTSYNAPSE_LLM_RPM"] = str(self.llm_rpm)
        env["CONTEXTSYNAPSE_MAX_GRAPHS"] = str(self.max_graphs_in_memory)
        env["CONTEXTSYNAPSE_WB_FLUSH_INTERVAL"] = str(self.write_behind_interval)
        if self.s3_bucket:
            env["CONTEXTSYNAPSE_S3_BUCKET"] = self.s3_bucket
            env["CONTEXTSYNAPSE_S3_REGION"] = self.s3_region
            env["CONTEXTSYNAPSE_S3_PREFIX"] = self.s3_prefix
        if self.gcs_bucket:
            env["CONTEXTSYNAPSE_GCS_BUCKET"] = self.gcs_bucket
        return env


def _load_from_yaml() -> Optional[Dict[str, Any]]:
    """Try to load deploy_settings.yaml from project root."""
    paths = [
        Path("deploy_settings.yaml"),
        Path("config/deploy_settings.yaml"),
        Path("/etc/contextcore/deploy_settings.yaml"),
    ]
    for p in paths:
        if p.exists():
            try:
                import yaml
                with open(p) as f:
                    data = yaml.safe_load(f)
                logger.info("Loaded deploy config from %s", p)
                return data
            except Exception as e:
                logger.warning("Failed to load %s: %s", p, e)
    return None


def _load_from_env() -> Dict[str, Any]:
    """Load config from environment variables."""
    config = {}
    # Each entry: (new_CONTEXTSYNAPSE_key, old_AICONTEXTDB_key, config_key)
    mapping = [
        ("CONTEXTSYNAPSE_DEPLOY_MODE", "AICONTEXTDB_DEPLOY_MODE", "mode"),
        ("CONTEXTSYNAPSE_REDIS_URL", "AICONTEXTDB_REDIS_URL", "redis_url"),
        ("CONTEXTSYNAPSE_STORAGE_BACKEND", "AICONTEXTDB_STORAGE_BACKEND", "storage_backend"),
        ("CONTEXTSYNAPSE_STORAGE_PATH", "AICONTEXTDB_STORAGE_PATH", "storage_path"),
        ("CONTEXTSYNAPSE_S3_BUCKET", "AICONTEXTDB_S3_BUCKET", "s3_bucket"),
        ("CONTEXTSYNAPSE_S3_REGION", "AICONTEXTDB_S3_REGION", "s3_region"),
        ("CONTEXTSYNAPSE_S3_PREFIX", "AICONTEXTDB_S3_PREFIX", "s3_prefix"),
        ("CONTEXTSYNAPSE_S3_ENDPOINT", "AICONTEXTDB_S3_ENDPOINT", "s3_endpoint"),
        ("CONTEXTSYNAPSE_GCS_BUCKET", "AICONTEXTDB_GCS_BUCKET", "gcs_bucket"),
        ("CONTEXTSYNAPSE_GCS_PREFIX", "AICONTEXTDB_GCS_PREFIX", "gcs_prefix"),
        (None, "QDRANT_URL", "qdrant_url"),
        ("CONTEXTSYNAPSE_GRAPH_BACKEND", "AICONTEXTDB_GRAPH_BACKEND", "graph_backend"),
        ("CONTEXTSYNAPSE_LLM_MAX_CONCURRENT", "AICONTEXTDB_LLM_MAX_CONCURRENT", "llm_max_concurrent"),
        ("CONTEXTSYNAPSE_LLM_RPM", "AICONTEXTDB_LLM_RPM", "llm_rpm"),
        ("CONTEXTSYNAPSE_MAX_GRAPHS", "AICONTEXTDB_MAX_GRAPHS", "max_graphs_in_memory"),
        ("CONTEXTSYNAPSE_WB_FLUSH_INTERVAL", "AICONTEXTDB_WB_FLUSH_INTERVAL", "write_behind_interval"),
        (None, "API_PORT", "api_port"),
        ("CONTEXTSYNAPSE_LOG_LEVEL", "AICONTEXTDB_LOG_LEVEL", "log_level"),
    ]
    for new_env_key, old_env_key, config_key in mapping:
        val = (os.environ.get(new_env_key) if new_env_key else None) or os.environ.get(old_env_key)
        if val is not None:
            # Convert numeric fields
            if config_key in ("api_port", "workers", "llm_max_concurrent", "llm_rpm",
                              "max_graphs_in_memory", "write_behind_interval", "ingestion_workers"):
                try:
                    val = int(val)
                except ValueError:
                    pass
            config[config_key] = val
    return config


# Singleton
_config: Optional[DeployConfig] = None


def get_deploy_config() -> DeployConfig:
    """Get deployment config — YAML file → env vars → defaults."""
    global _config
    if _config is not None:
        return _config

    config = DeployConfig()

    # Layer 1: YAML file
    yaml_data = _load_from_yaml()
    if yaml_data:
        for key, val in yaml_data.items():
            if hasattr(config, key):
                setattr(config, key, val)

    # Layer 2: Environment variables (override YAML)
    env_data = _load_from_env()
    for key, val in env_data.items():
        if hasattr(config, key):
            setattr(config, key, val)

    # Auto-detect mode
    if not config.mode or config.mode == MODE_LOCAL:
        if os.environ.get("KUBERNETES_SERVICE_HOST"):
            config.mode = MODE_K8S
        elif os.path.exists("/.dockerenv"):
            config.mode = MODE_DOCKER

    # Auto-enable Redis
    config.redis_enabled = bool(config.redis_url)

    logger.info("[DEPLOY] Mode=%s, storage=%s, redis=%s", config.mode, config.storage_backend, config.redis_enabled)
    _config = config
    return _config
