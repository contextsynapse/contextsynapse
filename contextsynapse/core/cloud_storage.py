"""
Cloud Storage Adapter — save/load graphs to S3, GCS, or Azure Blob.

Replaces local disk I/O for K8s deployments where pods are ephemeral.
Uses local disk as a cache layer — graphs are loaded from cloud on first
access and saved back on flush.

Usage:
    from contextsynapse.core.cloud_storage import get_storage_adapter

    adapter = get_storage_adapter()
    adapter.save(graph_name, local_path)   # upload to cloud
    adapter.load(graph_name, local_path)   # download from cloud
    adapter.exists(graph_name)             # check if exists in cloud
    adapter.delete(graph_name)             # remove from cloud
"""

from __future__ import annotations

import logging
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class StorageAdapter(ABC):
    """Abstract base for graph file storage."""

    @abstractmethod
    def save(self, graph_name: str, local_path: str) -> bool: ...

    @abstractmethod
    def load(self, graph_name: str, local_path: str) -> bool: ...

    @abstractmethod
    def exists(self, graph_name: str) -> bool: ...

    @abstractmethod
    def delete(self, graph_name: str) -> bool: ...

    @abstractmethod
    def list_graphs(self) -> List[str]: ...


class LocalStorageAdapter(StorageAdapter):
    """Local disk storage — default for dev and Docker."""

    def __init__(self, base_path: str = "contextcore_data"):
        self._base = Path(base_path)

    def save(self, graph_name: str, local_path: str) -> bool:
        # Already on local disk — nothing to do
        return True

    def load(self, graph_name: str, local_path: str) -> bool:
        return Path(local_path).exists()

    def exists(self, graph_name: str) -> bool:
        ns_dir = self._base / "namespaces" / graph_name
        return any(ns_dir.glob("graph.*")) if ns_dir.exists() else False

    def delete(self, graph_name: str) -> bool:
        ns_dir = self._base / "namespaces" / graph_name
        if ns_dir.exists():
            shutil.rmtree(ns_dir)
            return True
        return False

    def list_graphs(self) -> List[str]:
        ns_dir = self._base / "namespaces"
        if not ns_dir.exists():
            return []
        return [d.name for d in ns_dir.iterdir() if d.is_dir() and any(d.glob("graph.*"))]


class S3StorageAdapter(StorageAdapter):
    """Amazon S3 / MinIO / Cloudflare R2 storage."""

    def __init__(self, bucket: str, prefix: str = "graphs/",
                 region: str = "us-east-1", endpoint: str = ""):
        self._bucket = bucket
        self._prefix = prefix
        self._region = region
        self._endpoint = endpoint
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            kwargs = {"region_name": self._region}
            if self._endpoint:
                kwargs["endpoint_url"] = self._endpoint
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key(self, graph_name: str, filename: str = "graph.json") -> str:
        return f"{self._prefix}{graph_name}/{filename}"

    def save(self, graph_name: str, local_path: str) -> bool:
        try:
            s3 = self._get_client()
            p = Path(local_path)
            if p.is_file():
                s3.upload_file(str(p), self._bucket, self._key(graph_name, p.name))
            elif p.is_dir():
                for f in p.glob("*"):
                    if f.is_file():
                        s3.upload_file(str(f), self._bucket, self._key(graph_name, f.name))
            logger.debug("[S3] Saved graph '%s' to s3://%s/%s", graph_name, self._bucket, self._prefix)
            return True
        except Exception as e:
            logger.error("[S3] Failed to save '%s': %s", graph_name, e)
            return False

    def load(self, graph_name: str, local_path: str) -> bool:
        try:
            s3 = self._get_client()
            p = Path(local_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            # Try common graph file names
            for fname in ["graph.json", "graph.h5", "graph.pkl"]:
                try:
                    s3.download_file(self._bucket, self._key(graph_name, fname), str(p.parent / fname))
                    logger.debug("[S3] Loaded graph '%s' from S3", graph_name)
                    return True
                except s3.exceptions.ClientError:
                    continue
            return False
        except Exception as e:
            logger.error("[S3] Failed to load '%s': %s", graph_name, e)
            return False

    def exists(self, graph_name: str) -> bool:
        try:
            s3 = self._get_client()
            s3.head_object(Bucket=self._bucket, Key=self._key(graph_name))
            return True
        except Exception:
            return False

    def delete(self, graph_name: str) -> bool:
        try:
            s3 = self._get_client()
            # List and delete all objects under the prefix
            resp = s3.list_objects_v2(Bucket=self._bucket, Prefix=self._key(graph_name, ""))
            for obj in resp.get("Contents", []):
                s3.delete_object(Bucket=self._bucket, Key=obj["Key"])
            return True
        except Exception as e:
            logger.error("[S3] Failed to delete '%s': %s", graph_name, e)
            return False

    def list_graphs(self) -> List[str]:
        try:
            s3 = self._get_client()
            resp = s3.list_objects_v2(Bucket=self._bucket, Prefix=self._prefix, Delimiter="/")
            graphs = []
            for prefix in resp.get("CommonPrefixes", []):
                name = prefix["Prefix"].replace(self._prefix, "").strip("/")
                if name:
                    graphs.append(name)
            return graphs
        except Exception:
            return []


class GCSStorageAdapter(StorageAdapter):
    """Google Cloud Storage."""

    def __init__(self, bucket: str, prefix: str = "graphs/"):
        self._bucket_name = bucket
        self._prefix = prefix
        self._client = None

    def _get_bucket(self):
        if self._client is None:
            from google.cloud import storage
            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    def save(self, graph_name: str, local_path: str) -> bool:
        try:
            bucket = self._get_bucket()
            p = Path(local_path)
            if p.is_file():
                blob = bucket.blob(f"{self._prefix}{graph_name}/{p.name}")
                blob.upload_from_filename(str(p))
            elif p.is_dir():
                for f in p.glob("*"):
                    if f.is_file():
                        blob = bucket.blob(f"{self._prefix}{graph_name}/{f.name}")
                        blob.upload_from_filename(str(f))
            return True
        except Exception as e:
            logger.error("[GCS] Failed to save '%s': %s", graph_name, e)
            return False

    def load(self, graph_name: str, local_path: str) -> bool:
        try:
            bucket = self._get_bucket()
            p = Path(local_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            for fname in ["graph.json", "graph.h5"]:
                blob = bucket.blob(f"{self._prefix}{graph_name}/{fname}")
                if blob.exists():
                    blob.download_to_filename(str(p.parent / fname))
                    return True
            return False
        except Exception as e:
            logger.error("[GCS] Failed to load '%s': %s", graph_name, e)
            return False

    def exists(self, graph_name: str) -> bool:
        try:
            bucket = self._get_bucket()
            blob = bucket.blob(f"{self._prefix}{graph_name}/graph.json")
            return blob.exists()
        except Exception:
            return False

    def delete(self, graph_name: str) -> bool:
        try:
            bucket = self._get_bucket()
            blobs = bucket.list_blobs(prefix=f"{self._prefix}{graph_name}/")
            for blob in blobs:
                blob.delete()
            return True
        except Exception:
            return False

    def list_graphs(self) -> List[str]:
        try:
            bucket = self._get_bucket()
            blobs = bucket.list_blobs(prefix=self._prefix, delimiter="/")
            # Need to consume the iterator to get prefixes
            list(blobs)
            return [p.replace(self._prefix, "").strip("/") for p in blobs.prefixes]
        except Exception:
            return []


class AzureBlobStorageAdapter(StorageAdapter):
    """Azure Blob Storage."""

    def __init__(self, container: str, prefix: str = "graphs/",
                 connection_string: str = "", account_url: str = ""):
        self._container_name = container
        self._prefix = prefix
        self._connection_string = connection_string
        self._account_url = account_url
        self._client = None

    def _get_container(self):
        if self._client is None:
            from azure.storage.blob import BlobServiceClient
            if self._connection_string:
                service = BlobServiceClient.from_connection_string(self._connection_string)
            elif self._account_url:
                from azure.identity import DefaultAzureCredential
                service = BlobServiceClient(self._account_url, credential=DefaultAzureCredential())
            else:
                raise ValueError("Azure: set CONTEXTSYNAPSE_AZURE_CONNECTION_STRING or CONTEXTSYNAPSE_AZURE_ACCOUNT_URL")
            self._client = service.get_container_client(self._container_name)
        return self._client

    def _blob_name(self, graph_name: str, filename: str = "graph.json") -> str:
        return f"{self._prefix}{graph_name}/{filename}"

    def save(self, graph_name: str, local_path: str) -> bool:
        try:
            container = self._get_container()
            p = Path(local_path)
            if p.is_file():
                with open(p, "rb") as f:
                    container.upload_blob(self._blob_name(graph_name, p.name), f, overwrite=True)
            elif p.is_dir():
                for f in p.glob("*"):
                    if f.is_file():
                        with open(f, "rb") as fh:
                            container.upload_blob(self._blob_name(graph_name, f.name), fh, overwrite=True)
            logger.debug("[AZURE] Saved graph '%s' to %s/%s", graph_name, self._container_name, self._prefix)
            return True
        except Exception as e:
            logger.error("[AZURE] Failed to save '%s': %s", graph_name, e)
            return False

    def load(self, graph_name: str, local_path: str) -> bool:
        try:
            container = self._get_container()
            p = Path(local_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            for fname in ["graph.json", "graph.h5", "graph.pkl"]:
                blob_name = self._blob_name(graph_name, fname)
                blob = container.get_blob_client(blob_name)
                try:
                    data = blob.download_blob().readall()
                    with open(p.parent / fname, "wb") as f:
                        f.write(data)
                    logger.debug("[AZURE] Loaded graph '%s' from Azure", graph_name)
                    return True
                except Exception:
                    continue
            return False
        except Exception as e:
            logger.error("[AZURE] Failed to load '%s': %s", graph_name, e)
            return False

    def exists(self, graph_name: str) -> bool:
        try:
            container = self._get_container()
            blob = container.get_blob_client(self._blob_name(graph_name))
            blob.get_blob_properties()
            return True
        except Exception:
            return False

    def delete(self, graph_name: str) -> bool:
        try:
            container = self._get_container()
            blobs = container.list_blobs(name_starts_with=f"{self._prefix}{graph_name}/")
            for blob in blobs:
                container.delete_blob(blob.name)
            return True
        except Exception as e:
            logger.error("[AZURE] Failed to delete '%s': %s", graph_name, e)
            return False

    def list_graphs(self) -> List[str]:
        try:
            container = self._get_container()
            blobs = container.list_blobs(name_starts_with=self._prefix)
            graphs = set()
            for blob in blobs:
                # Extract graph name from "graphs/my_graph/graph.json"
                parts = blob.name.replace(self._prefix, "").split("/")
                if parts and parts[0]:
                    graphs.add(parts[0])
            return sorted(graphs)
        except Exception:
            return []


# Singleton
_adapter: Optional[StorageAdapter] = None


def get_storage_adapter() -> StorageAdapter:
    """Get storage adapter based on deployment config."""
    global _adapter
    if _adapter is not None:
        return _adapter

    backend = os.environ.get("CONTEXTSYNAPSE_STORAGE_BACKEND") or os.environ.get("AICONTEXTDB_STORAGE_BACKEND", "local")

    if backend == "s3":
        _adapter = S3StorageAdapter(
            bucket=os.environ.get("CONTEXTSYNAPSE_S3_BUCKET") or os.environ.get("AICONTEXTDB_S3_BUCKET", ""),
            prefix=os.environ.get("CONTEXTSYNAPSE_S3_PREFIX") or os.environ.get("AICONTEXTDB_S3_PREFIX", "graphs/"),
            region=os.environ.get("CONTEXTSYNAPSE_S3_REGION") or os.environ.get("AICONTEXTDB_S3_REGION", "us-east-1"),
            endpoint=os.environ.get("CONTEXTSYNAPSE_S3_ENDPOINT") or os.environ.get("AICONTEXTDB_S3_ENDPOINT", ""),
        )
        logger.info("[STORAGE] Backend: S3 (bucket=%s)", _adapter._bucket)
    elif backend == "gcs":
        _adapter = GCSStorageAdapter(
            bucket=os.environ.get("CONTEXTSYNAPSE_GCS_BUCKET") or os.environ.get("AICONTEXTDB_GCS_BUCKET", ""),
            prefix=os.environ.get("CONTEXTSYNAPSE_GCS_PREFIX") or os.environ.get("AICONTEXTDB_GCS_PREFIX", "graphs/"),
        )
        logger.info("[STORAGE] Backend: GCS (bucket=%s)", _adapter._bucket_name)
    elif backend == "azure":
        _adapter = AzureBlobStorageAdapter(
            container=os.environ.get("CONTEXTSYNAPSE_AZURE_CONTAINER") or os.environ.get("AICONTEXTDB_AZURE_CONTAINER", ""),
            prefix=os.environ.get("CONTEXTSYNAPSE_AZURE_PREFIX") or os.environ.get("AICONTEXTDB_AZURE_PREFIX", "graphs/"),
            connection_string=os.environ.get("CONTEXTSYNAPSE_AZURE_CONNECTION_STRING") or os.environ.get("AICONTEXTDB_AZURE_CONNECTION_STRING", ""),
            account_url=os.environ.get("CONTEXTSYNAPSE_AZURE_ACCOUNT_URL") or os.environ.get("AICONTEXTDB_AZURE_ACCOUNT_URL", ""),
        )
        logger.info("[STORAGE] Backend: Azure Blob (container=%s)", _adapter._container_name)
    else:
        _adapter = LocalStorageAdapter(
            base_path=os.environ.get("CONTEXTSYNAPSE_STORAGE_PATH") or os.environ.get("AICONTEXTDB_STORAGE_PATH", "contextcore_data"),
        )
        logger.info("[STORAGE] Backend: local disk")

    return _adapter
