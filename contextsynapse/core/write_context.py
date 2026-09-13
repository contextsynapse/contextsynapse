"""
Write-path provenance context using Python contextvars.

Set at API/gateway/tool boundaries, read at storage layer.
Ensures every graph write is tagged with origin metadata.
"""
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

# Region identifier — set via AICONTEXTDB_REGION env var or defaults to "local"
REGION = os.environ.get("CONTEXTSYNAPSE_REGION") or os.environ.get("AICONTEXTDB_REGION", "local")


@dataclass(frozen=True)
class WriteContext:
    agent_id: str = ""
    origin: str = "unknown"   # tool, aiql, ingest, a2a, api, system
    verified: bool = False     # True if from trusted path
    region: str = ""           # populated from REGION at set time


_write_ctx: ContextVar[Optional[WriteContext]] = ContextVar("_write_ctx", default=None)


def set_write_context(agent_id: str = "", origin: str = "unknown", verified: bool = False):
    """Set provenance context for the current async task / thread."""
    _write_ctx.set(WriteContext(agent_id=agent_id, origin=origin, verified=verified, region=REGION))


def get_write_context() -> Optional[WriteContext]:
    """Get the current write context, or None."""
    return _write_ctx.get()


def clear_write_context():
    """Clear write context (call in finally blocks)."""
    _write_ctx.set(None)
