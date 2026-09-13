"""
Connector Ingestor Registry — maps connector_type to ConnectorIngestor subclass.
"""

from typing import Dict, Optional, Type
from .base import ConnectorIngestor, ConnectorGraphResult

__all__ = ["ConnectorIngestor", "ConnectorGraphResult", "get_ingestor", "CONNECTOR_INGEST_MAP"]

CONNECTOR_INGEST_MAP: Dict[str, Type[ConnectorIngestor]] = {}


def register_ingestor(connector_type: str, cls: Type[ConnectorIngestor]):
    CONNECTOR_INGEST_MAP[connector_type] = cls


def get_ingestor(connector_type: str, config: dict, credentials: dict) -> Optional[ConnectorIngestor]:
    cls = CONNECTOR_INGEST_MAP.get(connector_type)
    if cls:
        return cls(config, credentials)
    return None


# Register implemented connectors only
_CONNECTORS = [
    ("sap", "sap_ingestor", "SAPIngestor"),
    ("chatgpt", "chatgpt_memory", "ChatGPTMemoryIngestor"),
    ("claude", "claude_memory", "ClaudeMemoryIngestor"),
]

import importlib as _importlib
for _ctype, _module, _cls_name in _CONNECTORS:
    try:
        _mod = _importlib.import_module(f".{_module}", package=__name__)
        _cls = getattr(_mod, _cls_name)
        register_ingestor(_ctype, _cls)
    except (ImportError, ModuleNotFoundError, AttributeError):
        pass
