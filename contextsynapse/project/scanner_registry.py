"""Scanner plugin registry for domain-specific scanners.

Scanners are StageOperator subclasses that extract domain-specific
nodes and edges (e.g. SDLC repo scan, process SOP scan).

Usage:
    from contextsynapse.project.scanner_registry import get_scanner_registry

    # Register (at module import time or plugin load):
    get_scanner_registry().register("sop_parser", SOPParserOperator)

    # Resolve (at pipeline composition time):
    scanners = get_scanner_registry().resolve(schema)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


class ScannerRegistry:
    """Registers and resolves domain-specific scanners."""

    def __init__(self):
        self._scanners: Dict[str, Type] = {}

    def register(self, name: str, scanner_class: Type) -> None:
        """Register a scanner class by name."""
        self._scanners[name] = scanner_class
        logger.info("Registered scanner: %s", name)

    def resolve(self, schema: Any) -> List:
        """Return scanner instances for scanners declared in schema."""
        if schema is None:
            return []
        scanners_section = getattr(schema, "scanners", None)
        if not scanners_section:
            return []
        instances = []
        for name in scanners_section:
            scanner_cls = self._scanners.get(name)
            if scanner_cls is not None:
                instances.append(scanner_cls())
            else:
                logger.warning(
                    "Scanner '%s' declared in schema but not registered", name
                )
        return instances


_scanner_registry: Optional[ScannerRegistry] = None


def get_scanner_registry() -> ScannerRegistry:
    """Module-level singleton."""
    global _scanner_registry
    if _scanner_registry is None:
        _scanner_registry = ScannerRegistry()
    return _scanner_registry
